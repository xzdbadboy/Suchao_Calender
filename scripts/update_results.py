#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""苏超淘汰赛完赛后更新比分。

只在数据源确认完赛后写入最终比分。加时、点球未结束或字段不完整时不写。
决胜场（次回合、总决赛）若 90 分钟打平，会等到加时/点球字段齐全，或完赛超过 3 小时仍无这些字段，才写入。
出线后同步半决赛/决赛对阵，并更新 README。确认有变化后自动提交并推送。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICS_PATH = ROOT / "2026_Suchao.ics"
README_PATH = ROOT / "README.md"
LOG_PATH = ROOT / "logs" / "update_results.log"
FALLBACK_LOG = Path("/var/log/suchao-update.log")
LOCK_PATH = Path("/tmp/suchao-update.lock")
API = "https://api.dongqiudi.com/data/tab/league/new/8647"
MARKER_START = "<!-- KNOCKOUT_AUTO_START -->"
MARKER_END = "<!-- KNOCKOUT_AUTO_END -->"
BJ = timezone(timedelta(hours=8))
ICON_BASE = "https://raw.githubusercontent.com/xzdbadboy/Suchao_Calender/main/icon/"
ICONS = {
    "南京": "nanjing", "无锡": "wuxi", "徐州": "xuzhou", "常州": "changzhou",
    "苏州": "suzhou", "南通": "nantong", "连云港": "lianyungang", "淮安": "huaian",
    "盐城": "yancheng", "扬州": "yangzhou", "镇江": "zhenjiang", "泰州": "taizhou",
    "宿迁": "suqian",
}
SEED = ["无锡", "宿迁", "常州", "南通", "泰州", "苏州", "盐城", "徐州"]
BRACKET = {"1": ("A", "B"), "2": ("C", "D")}
IN_PLAY = {"1H", "2H", "HT", "ET", "ET1", "ET2", "E1", "E2", "PEN", "PSO", "PENS", "LIVE", "INPLAY", "1ST", "2ND", "EXTRA"}
FINISHED = {"FT", "AET", "AP", "FT_PEN", "PEN_FT", "AFTER_PEN", "FULLTIME", "FULL_TIME", "ENDED", "FINISHED"}

log = logging.getLogger("suchao")


def setup_log() -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    for path in (LOG_PATH, FALLBACK_LOG):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            continue


def acquire_lock():
    try:
        fh = LOCK_PATH.open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("已有更新任务在运行，跳过")
        return None
    except OSError as exc:
        log.warning("无法创建锁文件，继续执行：%s", exc)
        return None
    return fh


def now_bj() -> datetime:
    return datetime.now(BJ)


def city(name: str | None) -> str | None:
    if not name:
        return None
    text = name.replace("（主）", "").replace("(主)", "").replace("队", "").strip()
    if not text or any(flag in text for flag in ("胜者", "待定", "/", "对")):
        return None
    return text


def pair(a, b):
    if a is None or b is None or str(a).strip() == "" or str(b).strip() == "":
        return None
    try:
        return int(str(a).strip()), int(str(b).strip())
    except (TypeError, ValueError):
        return None


def parse_utc(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).astimezone(BJ)
        except ValueError:
            continue
    return None


def ics_stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def unfold(text: str) -> list[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def desc_items(desc: str) -> list[tuple[str, str]]:
    items = []
    for part in desc.split("\\n"):
        if ": " in part:
            key, val = part.split(": ", 1)
        elif ":" in part:
            key, val = part.split(":", 1)
            val = val.strip()
        else:
            continue
        items.append((key.strip(), val.strip()))
    return items


def desc_get(desc: str, key: str) -> str:
    for k, v in desc_items(desc):
        if k == key:
            return v
    return ""


def encode_desc(items: list[tuple[str, str]]) -> str:
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return "\\n".join(f"{key}: {esc(val)}" for key, val in items)


class Event:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self._reindex()

    def _reindex(self) -> None:
        self.fields: dict[str, list[str]] = {}
        for line in self.lines[1:-1]:
            if ":" not in line:
                continue
            key = line.split(":", 1)[0].split(";", 1)[0]
            self.fields.setdefault(key, []).append(line)

    def summary(self) -> str:
        vals = self.fields.get("SUMMARY")
        return vals[-1].split(":", 1)[1] if vals else ""

    def description(self) -> str:
        vals = self.fields.get("DESCRIPTION")
        return vals[-1].split(":", 1)[1] if vals else ""

    def prefix(self) -> str:
        summary = self.summary()
        return summary.split("-", 1)[0] if "-" in summary else summary

    def is_knockout(self) -> bool:
        label = self.prefix()
        return "阶段: 淘汰赛" in self.description() or label.startswith(("1/4决赛", "半决赛", "总决赛"))

    def start(self) -> datetime | None:
        vals = self.fields.get("DTSTART")
        if not vals:
            return None
        stamp = vals[-1].rsplit(":", 1)[-1]
        try:
            return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=BJ)
        except ValueError:
            return None

    def teams(self) -> tuple[str | None, str | None]:
        duizhen = desc_get(self.description(), "对阵")
        if " vs " in duizhen:
            left, right = duizhen.split(" vs ", 1)
            return city(left), city(right)
        return None, None

    def mentioned(self) -> list[str]:
        text = self.summary() + self.description()
        return [name for name in ICONS if name in text]

    def pair_teams(self) -> tuple[str | None, str | None]:
        home, away = self.teams()
        if home and away:
            return home, away
        names = self.mentioned()
        if len(names) == 2:
            return names[0], names[1]
        return None, None

    def slot(self) -> str:
        matched = re.search(r"签位: ([A-D])", self.description())
        return matched.group(1) if matched else ""

    def round_name(self) -> str:
        return desc_get(self.description(), "轮次") or self.prefix()

    def is_deciding(self) -> bool:
        label = self.round_name() + self.prefix()
        return "次回合" in label or "总决赛" in label

    def existing_score(self) -> str:
        summary = self.summary()
        if "-" not in summary:
            return ""
        tail = summary.split("-", 1)[1].strip()
        return tail if re.search(r"\d+:\d+", tail) else ""

    def serialize(self) -> str:
        return "\n".join(self.lines)


def parse_calendar(text: str) -> list[Event | str]:
    lines = unfold(text)
    parts: list[Event | str] = []
    buf: list[str] = []
    event: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            if buf:
                parts.append("\n".join(buf))
                buf = []
            event = [line]
            continue
        if event is not None:
            event.append(line)
            if line == "END:VEVENT":
                parts.append(Event(event))
                event = None
            continue
        buf.append(line)
    if event is not None:
        buf.extend(event)
    if buf:
        parts.append("\n".join(buf))
    return parts


def dump_calendar(parts: list[Event | str]) -> str:
    text = "\n".join(part.serialize() if isinstance(part, Event) else part for part in parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


def fetch_api(starts: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    errors = 0
    for start in starts:
        query = urllib.parse.urlencode({
            "start": start, "version": "576", "init": "1", "wfrom": "2", "from": "msite_com",
        })
        req = urllib.request.Request(API + "?" + query, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://m.dongqiudi.com",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            errors += 1
            log.warning("数据源请求失败 %s: %s", start, exc)
            continue
        for item in data.get("list") or []:
            mid = str(item.get("match_id") or item.get("relate_id") or "")
            if mid and item.get("team_A_name"):
                found[mid] = item
    if not found and errors:
        raise RuntimeError("数据源全部请求失败")
    return list(found.values())


def interpret(item: dict) -> dict:
    fs = pair(item.get("fs_A"), item.get("fs_B"))
    ets_raw = pair(item.get("ets_A"), item.get("ets_B"))
    ps = pair(item.get("ps_A"), item.get("ps_B"))
    period = str(item.get("minute_period") or "").upper()
    partial = False
    if (str(item.get("ets_A") or "").strip() or str(item.get("ets_B") or "").strip()) and ets_raw is None:
        partial = True
    if (str(item.get("ps_A") or "").strip() or str(item.get("ps_B") or "").strip()) and ps is None:
        partial = True
    if ets_raw == (0, 0) and period in {"FT", ""}:
        ets_raw = None
    if ps == (0, 0) and period not in {"AP", "FT_PEN", "PEN_FT", "AFTER_PEN"}:
        ps = None
    et_score = None
    et_happened = False
    if fs and ets_raw and not partial:
        if ets_raw[0] >= fs[0] and ets_raw[1] >= fs[1]:
            et_score = ets_raw
        else:
            et_score = (fs[0] + ets_raw[0], fs[1] + ets_raw[1])
        et_happened = True
    return {
        "fs": fs,
        "et": et_score,
        "et_happened": et_happened,
        "ps": ps,
        "display": et_score or fs,
        "partial": partial,
        "home": city(item.get("team_A_name")),
        "away": city(item.get("team_B_name")),
        "start": parse_utc(str(item.get("start_play") or "")),
        "end": parse_utc(str(item.get("end_play") or "")),
        "status": str(item.get("status") or ""),
        "period": period,
        "round": str(item.get("round_name") or ""),
        "title": str(item.get("match_title") or ""),
        "leg": str(item.get("agg_first_or_second") or ""),
        "id": str(item.get("match_id") or ""),
    }


def stage_of(info: dict) -> str:
    label = info["round"] + info["title"]
    if "1/4" in label or "八强" in label:
        return "qf"
    if "半决赛" in label:
        return "sf"
    if "决赛" in label:
        return "final"
    return ""


def knockout_infos(matches: list[dict]) -> list[dict]:
    infos = [interpret(item) for item in matches]
    return [info for info in infos if stage_of(info)]


def result_ready(info: dict, deciding: bool, now: datetime) -> tuple[bool, str]:
    if info["partial"]:
        return False, "加时或点球字段不完整"
    status = info["status"].lower()
    if status in {"fixture", "playing", "live", "postponed", "delayed", "suspended", "cancelled", "canceled", "abandoned", ""}:
        return False, f"状态 {info['status'] or '空'}"
    if status not in {"played", "finished", "ended", "complete", "completed"}:
        return False, f"状态未确认完赛（{info['status']}）"
    if info["period"] in IN_PLAY:
        return False, f"仍在进行（{info['period']}）"
    if info["fs"] is None or info["display"] is None:
        return False, "无比分"
    if info["period"] and info["period"] not in FINISHED:
        return False, f"完赛标记未识别（{info['period']}）"
    if not info["period"] and not info["end"]:
        return False, "缺少完赛时间"
    display = info["display"]
    level = display[0] == display[1]
    if deciding and level and not info["ps"]:
        stale_base = info["end"] or (info["start"] + timedelta(hours=6) if info["start"] else None)
        if stale_base and now - stale_base >= timedelta(hours=3):
            return True, "平局且超过3小时仍无加时/点球字段，按现有比分写入"
        return False, "决胜场平局，等待加时或点球"
    return True, "完赛"


def format_result(info: dict) -> dict:
    home, away = info["home"], info["away"]
    fs, display, ps = info["fs"], info["display"], info["ps"]
    if not home or not away or not fs or not display:
        raise ValueError("比分缺少球队或数字")
    if ps:
        tail = f"{home} {display[0]}:{display[1]} {away} (点球 {ps[0]}:{ps[1]})"
        score_line = f"{home}队 {display[0]}:{display[1]} {away}队（点球 {ps[0]}:{ps[1]}）"
    elif info["et_happened"]:
        tail = f"{home} {display[0]}:{display[1]} {away}"
        score_line = f"{home}队 {display[0]}:{display[1]} {away}队（加时）"
    else:
        tail = f"{home} {fs[0]}:{fs[1]} {away}"
        score_line = f"{home}队 {fs[0]}:{fs[1]} {away}队"
    extra: list[tuple[str, str]] = []
    if ps or info["et_happened"]:
        extra.append(("90分钟", f"{fs[0]}:{fs[1]}"))
    if info["et_happened"] and info["et"]:
        extra.append(("加时", f"{info['et'][0]}:{info['et'][1]}"))
    if ps:
        extra.append(("点球", f"{ps[0]}:{ps[1]}"))
    return {"tail": tail, "score_line": score_line, "extra": extra}


def goals_for(team: str, info: dict) -> int | None:
    display = info["display"]
    if not display or team not in (info["home"], info["away"]):
        return None
    return display[0] if team == info["home"] else display[1]


def winner_of(team_a: str, team_b: str, leg1: dict | None, leg2: dict | None, now: datetime) -> tuple[str | None, str]:
    if not leg1 or not leg2:
        return None, "轮次未齐"
    ok1, why1 = result_ready(leg1, False, now)
    ok2, why2 = result_ready(leg2, True, now)
    if not ok1 or not ok2:
        return None, f"首回合{why1}；次回合{why2}"
    totals = {}
    for team in (team_a, team_b):
        g1, g2 = goals_for(team, leg1), goals_for(team, leg2)
        if g1 is None or g2 is None:
            return None, "总比分无法对应球队"
        totals[team] = g1 + g2
    label = f"总比分 {team_a} {totals[team_a]}:{totals[team_b]} {team_b}"
    if totals[team_a] != totals[team_b]:
        winner = team_a if totals[team_a] > totals[team_b] else team_b
        return winner, label
    ps = leg2.get("ps")
    if ps and leg2.get("home") and leg2.get("away"):
        pens = {leg2["home"]: ps[0], leg2["away"]: ps[1]}
        if pens.get(team_a) != pens.get(team_b):
            winner = team_a if pens.get(team_a, -1) > pens.get(team_b, -1) else team_b
            return winner, f"{label}，点球 {pens.get(team_a)}:{pens.get(team_b)}"
    return None, f"{label}，胜者未定"


def find_api(infos: list[dict], teams: set[str], day: str | None = None, leg: str | None = None, stage: str | None = None, title_word: str | None = None) -> dict | None:
    found = []
    for info in infos:
        pair_teams = {info["home"], info["away"]}
        if None in pair_teams or pair_teams != teams:
            continue
        if day and (not info["start"] or info["start"].strftime("%Y%m%d") != day):
            continue
        if leg and info["leg"] and info["leg"] != leg:
            continue
        if stage and stage_of(info) != stage:
            continue
        if title_word and title_word not in (info["title"] + info["round"]):
            continue
        found.append(info)
    return found[0] if found else None


def set_prop(event: Event, name: str, value: str, params: str = "") -> None:
    new_line = f"{name}{params}:{value}"
    lines = []
    replaced = False
    for line in event.lines:
        current = line.split(":", 1)[0].split(";", 1)[0]
        if current == name:
            if not replaced:
                lines.append(new_line)
                replaced = True
            continue
        lines.append(line)
    if not replaced:
        lines.insert(len(lines) - 1, new_line)
    event.lines = lines
    event._reindex()


def set_attachments(event: Event, teams: list[str]) -> None:
    attach = [f"ATTACH;FMTTYPE=image/png:{ICON_BASE}{ICONS[name]}.png" for name in teams if name in ICONS]
    if len(attach) < 2:
        return
    lines = []
    inserted = False
    for line in event.lines:
        current = line.split(":", 1)[0].split(";", 1)[0]
        if current == "ATTACH":
            if not inserted:
                lines.extend(attach)
                inserted = True
            continue
        lines.append(line)
        if not inserted and current == "DTEND":
            lines.extend(attach)
            inserted = True
    event.lines = lines
    event._reindex()


def rewrite_description(event: Event, updates: dict[str, str], drop: set[str] | None = None) -> None:
    drop = drop or set()
    kept = [(k, v) for k, v in desc_items(event.description()) if k not in updates and k not in drop]
    merged = dict(kept)
    merged.update(updates)
    order = ["阶段", "轮次", "对阵", "比分", "90分钟", "加时", "点球", "两回合总比分", "签位", "地点", "时间", "更新时间"]
    items = []
    used = set()
    for key in order:
        if key in merged:
            items.append((key, merged[key]))
            used.add(key)
    for key, val in kept:
        if key not in used:
            items.append((key, val))
    notes = [
        part for part in event.description().split("\\n")
        if part and ":" not in part and "待" not in part
    ]
    update = merged.get("更新时间")
    items = [item for item in items if item[0] != "更新时间"]
    encoded = encode_desc(items)
    if notes:
        encoded += "\\n" + "\\n".join(notes)
    if update:
        encoded += f"\\n更新时间: {update}"
    set_prop(event, "DESCRIPTION", encoded)


def apply_time(event: Event, info: dict) -> None:
    if not info["start"]:
        return
    set_prop(event, "DTSTART", ics_stamp(info["start"]), ";TZID=Asia/Shanghai")
    set_prop(event, "DTEND", ics_stamp(info["start"] + timedelta(hours=2)), ";TZID=Asia/Shanghai")


def apply_score(event: Event, info: dict, stamp: str) -> str:
    formatted = format_result(info)
    set_prop(event, "SUMMARY", f"{event.prefix()}-{formatted['tail']}")
    updates = {
        "对阵": f"{info['home']}队(主) vs {info['away']}队",
        "比分": formatted["score_line"],
        "更新时间": stamp,
    }
    if info["start"]:
        updates["时间"] = f"北京时间 {info['start'].strftime('%H:%M')}"
    for key, val in formatted["extra"]:
        updates[key] = val
    drop = {"90分钟", "加时", "点球"} if not formatted["extra"] else set()
    rewrite_description(event, updates, drop)
    apply_time(event, info)
    set_attachments(event, [info["home"], info["away"]])
    return formatted["tail"]


def apply_pairing(event: Event, home: str | None, away: str | None, teams: list[str], info: dict | None, stamp: str) -> str:
    prefix = event.prefix()
    if home and away:
        set_prop(event, "SUMMARY", f"{prefix}-{home} vs {away}")
        duizhen = f"{home}队(主) vs {away}队"
        note = f"{prefix} {home} vs {away}"
        set_attachments(event, [home, away])
    else:
        shown = " 对 ".join(f"{name}队" for name in teams)
        set_prop(event, "SUMMARY", f"{prefix}-主客待定")
        duizhen = f"{shown}（主客场待官方公布）"
        note = f"{prefix}出线：{shown}（主客待定）"
        set_attachments(event, teams)
    updates = {"对阵": duizhen, "更新时间": stamp}
    rewrite_description(event, updates)
    if info:
        apply_time(event, info)
    return note


def event_day(event: Event) -> str | None:
    start = event.start()
    return start.strftime("%Y%m%d") if start else None


def refinement(old: str, new: str) -> bool:
    return bool(old) and "点球" not in old and "点球" in new and old.split()[0:3] == new.split()[0:3]


def render_block(events: list[Event], winners: dict[str, str], notes: dict[str, str]) -> str:
    ko = [event for event in events if event.is_knockout()]
    done = [event for event in ko if event.existing_score()]
    nxt = next((event for event in ko if not event.existing_score()), None)
    if nxt and nxt.start():
        nxt_text = f"{nxt.prefix()}（{nxt.start().strftime('%m月%d日 %H:%M')}）"
    elif nxt:
        nxt_text = nxt.prefix()
    else:
        nxt_text = "淘汰赛已全部完赛"
    rows = [
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 已完成轮次 | 常规赛第1-22轮已收官；淘汰赛已完成 {len(done)}/13 场 |",
        f"| 已进行比赛 | 78场常规赛 + {len(done)}场淘汰赛 |",
        "| 总计划比赛 | 78场常规赛 + 13场淘汰赛 |",
        f"| 完成率 | 常规赛 100%；淘汰赛 {len(done)}/13 |",
        f"| 下一阶段 | {nxt_text} |",
    ]
    table = ["| 阶段 | 日期 | 对阵 | 结果 |", "|------|------|------|------|"]
    for event in ko:
        start = event.start()
        when = start.strftime("%m-%d %H:%M") if start else "待定"
        home, away = event.teams()
        if home and away:
            pairing = f"{home} vs {away}"
        else:
            names = event.mentioned()
            pairing = " 对 ".join(names) if names else "待定"
        if event.existing_score():
            result = event.existing_score()
        elif "主客待定" in event.summary():
            result = "主客待定"
        else:
            result = "未赛"
        table.append(f"| {event.prefix()} | {when} | {pairing} | {result} |")
    qualify = []
    for slot in "ABCD":
        if slot in winners:
            qualify.append(f"{slot}组 {winners[slot]}（{notes.get(slot, '已出线')}）")
        else:
            qualify.append(f"{slot}组 待定")
    sf = []
    for num in ("1", "2"):
        event = next((item for item in ko if item.prefix() == f"半决赛{num}首回合"), None)
        if not event:
            continue
        home, away = event.teams()
        if home and away:
            sf.append(f"半决赛{num} {home} vs {away}")
        elif "主客待定" in event.summary():
            sf.append(f"半决赛{num} {' 对 '.join(event.mentioned())}（主客待定）")
        else:
            sf.append(f"半决赛{num} 待定")
    final = next((event for event in ko if event.prefix() == "总决赛"), None)
    final_text = "待定"
    if final:
        home, away = final.teams()
        if home and away:
            final_text = f"{home} vs {away}"
        elif "主客待定" in final.summary():
            final_text = f"{' 对 '.join(final.mentioned())}（主客待定）"
    body = [
        "## 📈 赛程进度",
        "",
        *rows,
        "",
        "比分在比赛结束（含加时、点球）并经数据源确认为完赛后自动写入，进行中比分不会提前更新。",
        "",
        "## 淘汰赛进展",
        "",
        *table,
        "",
        "**出线**：" + "；".join(qualify),
        "**半决赛**：" + ("；".join(sf) if sf else "待定"),
        f"**决赛**：{final_text}",
    ]
    return "\n".join(body).rstrip() + "\n"


def replace_block(readme: str, block: str) -> str:
    payload = f"{MARKER_START}\n{block}{MARKER_END}"
    start = readme.find(MARKER_START)
    end = readme.find(MARKER_END)
    if start >= 0 and end > start:
        return readme[:start] + payload + readme[end + len(MARKER_END):]
    anchor = "## ✨ 功能特性"
    if anchor in readme:
        return readme.replace(anchor, payload + "\n---\n\n" + anchor, 1)
    return readme.rstrip() + "\n\n" + payload + "\n"


def update_stamp(readme: str, now: datetime) -> str:
    line = f"**最后更新**：{now.strftime('%Y-%m-%d %H:%M')}"
    if re.search(r"\*\*最后更新\*\*：.*", readme):
        return re.sub(r"\*\*最后更新\*\*：.*", line, readme, count=1)
    return line + "\n" + readme


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", "-c", "user.name=Suchao Calendar", "-c", "user.email=calendar@suchao.local", *args],
        cwd=ROOT, text=True, capture_output=True, check=check,
    )


def tracked_dirty() -> str:
    return git("status", "--porcelain", "--", "2026_Suchao.ics", "README.md", check=False).stdout.strip()


def commit_and_push(message: str, dry_run: bool) -> None:
    diff = git("diff", "--", "2026_Suchao.ics", "README.md", check=False).stdout
    if diff.strip():
        if dry_run:
            log.info("演练：将提交 %s", message.splitlines()[0])
            return
        git("add", "--", "2026_Suchao.ics", "README.md")
        git("commit", "-m", message)
        log.info("已提交：%s", message.splitlines()[0])
    ahead = git("rev-list", "--count", "@{u}..HEAD", check=False)
    if ahead.returncode == 0 and ahead.stdout.strip() in {"", "0"}:
        log.info("没有需要推送的提交")
        return
    if dry_run:
        log.info("演练：将推送到 origin/main")
        return
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", "-c", "credential.helper=!gh auth git-credential", "push", "origin", "HEAD:main"],
        cwd=ROOT, text=True, capture_output=True, env=env, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("推送失败：" + (proc.stderr.strip() or proc.stdout.strip()))
    log.info("已推送到 origin/main")


def starts_for(now: datetime) -> list[str]:
    days = [now.date(), now.date() - timedelta(days=1)]
    for month, day in ((10, 2), (10, 10), (10, 16), (10, 23), (10, 30)):
        days.append(datetime(now.year, month, day).date())
    texts = []
    for day in days:
        text = f"{day.isoformat()} 16:00:00"
        if text not in texts:
            texts.append(text)
    return texts


def collect_groups(events: list[Event], infos: list[dict]) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for event in events:
        slot = event.slot()
        home, away = event.teams()
        if not slot or not home or not away:
            continue
        group = groups.setdefault(slot, {"teams": {home, away}, "events": {}, "api": {}})
        kind = "2" if event.is_deciding() else "1"
        group["events"][kind] = event
        group["teams"] = {home, away}
    for group in groups.values():
        for kind, event in group["events"].items():
            home, away = event.teams()
            info = find_api(infos, {home, away}, event_day(event), leg=kind, stage="qf")
            if info is None:
                info = find_api(infos, {home, away}, leg=kind, stage="qf", title_word="首回合" if kind == "1" else "次回合")
            group["api"][kind] = info
    return groups


def fill_fixture(events: list[Event], infos: list[dict], prefix: str, team_a: str | None, team_b: str | None, stage: str, leg: str, title_word: str, stamp: str, changes: list[str]) -> None:
    if not team_a or not team_b:
        return
    event = next((item for item in events if item.prefix() == prefix), None)
    if event is None or event.existing_score():
        return
    info = find_api(infos, {team_a, team_b}, leg=leg or None, stage=stage, title_word=title_word)
    current = event.teams()
    if info and current == (info["home"], info["away"]):
        return
    if not info and "主客待定" in event.summary() and set(event.mentioned()) == {team_a, team_b}:
        return
    ordered = sorted([team_a, team_b], key=lambda name: SEED.index(name) if name in SEED else 99)
    changes.append(apply_pairing(event, info["home"] if info else None, info["away"] if info else None, ordered, info, stamp))


def run(dry_run: bool, fixture: Path | None) -> int:
    if not ICS_PATH.exists():
        log.warning("日历文件不存在，可能是挂载断开：%s", ICS_PATH)
        return 0
    dirty = tracked_dirty()
    if dirty and not fixture:
        log.error("日历或 README 有未提交改动，为避免覆盖人工修改，本次跳过：\n%s", dirty)
        return 2
    matches = json.loads(fixture.read_text(encoding="utf-8")) if fixture else fetch_api(starts_for(now_bj()))
    infos = knockout_infos(matches)
    log.info("数据源淘汰赛场次 %s", len(infos))
    original = ICS_PATH.read_text(encoding="utf-8")
    parts = parse_calendar(original)
    if dump_calendar(parts) != original:
        raise RuntimeError("日历解析后无法原样写回，已停止，避免破坏常规赛")
    events = [part for part in parts if isinstance(part, Event)]
    ko = [event for event in events if event.is_knockout()]
    changes: list[str] = []
    now = now_bj()
    stamp = now.strftime("%Y/%m/%d")
    groups = collect_groups(ko, infos)

    for event in ko:
        home, away = event.teams()
        if not home or not away:
            continue
        info = find_api(infos, {home, away}, event_day(event))
        if info is None:
            info = find_api(infos, {home, away}, stage="qf" if event.prefix().startswith("1/4") else None)
        if info is None:
            log.info("未匹配到数据源：%s", event.summary())
            continue
        status = info["status"].lower()
        if info["start"] and event.start() and abs((info["start"] - event.start()).total_seconds()) >= 60 and status in {"fixture", "postponed", "delayed"}:
            apply_time(event, info)
            rewrite_description(event, {"时间": f"北京时间 {info['start'].strftime('%H:%M')}", "更新时间": stamp})
            changes.append(f"改期 {event.prefix()} {info['start'].strftime('%m-%d %H:%M')}")
            continue
        ready, why = result_ready(info, event.is_deciding(), now)
        if not ready:
            log.info("%s 不写入：%s", event.summary(), why)
            continue
        tail = format_result(info)["tail"]
        old = event.existing_score()
        if old == tail:
            log.info("比分已是最新：%s", event.summary())
            continue
        if old and not refinement(old, tail):
            log.error("本地比分与数据源不一致，不覆盖：%s | 数据源 %s", event.summary(), tail)
            continue
        apply_score(event, info, stamp)
        when = info["start"].strftime("%m月%d日") if info["start"] else event.prefix()
        changes.append(f"{when} {tail}")

    winners: dict[str, str] = {}
    notes: dict[str, str] = {}
    for slot, group in groups.items():
        teams = sorted(group["teams"], key=lambda name: SEED.index(name) if name in SEED else 99)
        if len(teams) != 2:
            continue
        winner, note = winner_of(teams[0], teams[1], group["api"].get("1"), group["api"].get("2"), now)
        notes[slot] = note
        if winner:
            winners[slot] = winner
            log.info("%s组出线：%s（%s）", slot, winner, note)

    fill_fixture(ko, infos, "半决赛1首回合", winners.get("A"), winners.get("B"), "sf", "1", "首回合", stamp, changes)
    fill_fixture(ko, infos, "半决赛1次回合", winners.get("A"), winners.get("B"), "sf", "2", "次回合", stamp, changes)
    fill_fixture(ko, infos, "半决赛2首回合", winners.get("C"), winners.get("D"), "sf", "1", "首回合", stamp, changes)
    fill_fixture(ko, infos, "半决赛2次回合", winners.get("C"), winners.get("D"), "sf", "2", "次回合", stamp, changes)

    sf_winners: dict[str, str] = {}
    for num in ("1", "2"):
        first = next((event for event in ko if event.prefix() == f"半决赛{num}首回合"), None)
        second = next((event for event in ko if event.prefix() == f"半决赛{num}次回合"), None)
        if not first or not second:
            continue
        team_a, team_b = first.pair_teams()
        if not team_a or not team_b:
            continue
        leg1 = find_api(infos, {team_a, team_b}, event_day(first), leg="1", stage="sf") or find_api(infos, {team_a, team_b}, stage="sf", title_word="首回合")
        leg2 = find_api(infos, {team_a, team_b}, event_day(second), leg="2", stage="sf") or find_api(infos, {team_a, team_b}, stage="sf", title_word="次回合")
        winner, note = winner_of(team_a, team_b, leg1, leg2, now)
        if winner:
            sf_winners[num] = winner
            log.info("半决赛%s出线：%s（%s）", num, winner, note)
    fill_fixture(ko, infos, "总决赛", sf_winners.get("1"), sf_winners.get("2"), "final", "", "决赛", stamp, changes)

    new_ics = dump_calendar(parts)
    readme = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else ""
    new_readme = update_stamp(replace_block(readme, render_block(events, winners, notes)), now) if changes else readme
    if new_ics == original and new_readme == readme:
        log.info("没有需要写入的完赛结果")
        if not dry_run and not fixture:
            commit_and_push("更新淘汰赛比分", dry_run)
        return 0
    # 常规赛事件必须保持原样
    old_regular = [part.serialize() for part in parse_calendar(original) if isinstance(part, Event) and not part.is_knockout()]
    new_regular = [part.serialize() for part in parts if isinstance(part, Event) and not part.is_knockout()]
    if old_regular != new_regular:
        raise RuntimeError("常规赛事件被改动，已停止")
    log.info("待更新 %s 项：%s", len(changes), "；".join(changes))
    if dry_run or fixture:
        log.info("演练模式，不写仓库文件")
        return 0
    ICS_PATH.write_text(new_ics, encoding="utf-8")
    if new_readme != readme:
        README_PATH.write_text(new_readme, encoding="utf-8")
    title = "更新淘汰赛：" + "、".join(changes[:4])
    if len(changes) > 4:
        title += f" 等{len(changes)}项"
    message = title + "\n\n" + "\n".join(f"- {item}" for item in changes) + "\n\n数据源已确认完赛或官方赛程已更新，自动写入。"
    commit_and_push(message, dry_run)
    return 0


def self_test() -> int:
    now = datetime(2026, 10, 11, 23, 0, tzinfo=BJ)
    base = {"team_A_name": "徐州队", "team_B_name": "无锡队", "status": "Played", "minute_period": "FT", "fs_A": "1", "fs_B": "0", "ets_A": "", "ets_B": "", "ps_A": "", "ps_B": "", "end_play": "2026-10-03 13:40:00", "start_play": "2026-10-03 11:40:00"}
    normal = interpret(base)
    assert result_ready(normal, False, now)[0]
    assert format_result(normal)["tail"] == "徐州 1:0 无锡"
    et = interpret({**base, "team_A_name": "无锡队", "team_B_name": "徐州队", "minute_period": "AET", "fs_A": "1", "fs_B": "1", "ets_A": "2", "ets_B": "1", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 14:20:00"})
    assert format_result(et)["score_line"] == "无锡队 2:1 徐州队（加时）"
    et_goals = interpret({**et_item(base), "ets_A": "1", "ets_B": "0"})
    assert format_result(et_goals)["tail"] == "无锡 2:1 徐州"
    pens = interpret({**base, "team_A_name": "无锡队", "team_B_name": "徐州队", "minute_period": "AP", "fs_A": "1", "fs_B": "1", "ets_A": "1", "ets_B": "1", "ps_A": "4", "ps_B": "3", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 14:40:00"})
    assert format_result(pens)["tail"] == "无锡 1:1 徐州 (点球 4:3)"
    assert "90分钟" in dict(format_result(pens)["extra"])
    playing = interpret({**base, "status": "Playing", "minute_period": "PEN", "fs_A": "1", "fs_B": "1", "ps_A": "3", "ps_B": "2"})
    assert not result_ready(playing, True, now)[0]
    partial = interpret({**base, "minute_period": "AP", "ps_A": "4", "ps_B": ""})
    assert not result_ready(partial, True, now)[0]
    level = interpret({**base, "team_A_name": "南通队", "team_B_name": "泰州队", "fs_A": "1", "fs_B": "1", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 13:45:00"})
    assert not result_ready(level, True, datetime(2026, 10, 11, 22, 0, tzinfo=BJ))[0]
    assert result_ready(level, True, datetime(2026, 10, 12, 8, 10, tzinfo=BJ))[0]
    assert result_ready(level, False, datetime(2026, 10, 11, 22, 0, tzinfo=BJ))[0]
    leg1 = interpret({**base, "fs_A": "1", "fs_B": "1"})
    leg2 = interpret({**base, "team_A_name": "无锡队", "team_B_name": "徐州队", "minute_period": "AP", "fs_A": "0", "fs_B": "0", "ets_A": "", "ets_B": "", "ps_A": "5", "ps_B": "4", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 14:40:00"})
    winner, note = winner_of("徐州", "无锡", leg1, leg2, now)
    assert winner == "无锡" and "点球" in note, note
    leg2_agg = interpret({**base, "team_A_name": "无锡队", "team_B_name": "徐州队", "fs_A": "2", "fs_B": "0", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 13:40:00"})
    winner, note = winner_of("徐州", "无锡", leg1, leg2_agg, now)
    assert winner == "无锡" and "总比分" in note, note
    assert winner_of("徐州", "无锡", leg1, None, now)[0] is None
    original = ICS_PATH.read_text(encoding="utf-8")
    assert dump_calendar(parse_calendar(original)) == original, "日历往返不稳定"
    print("self-test ok")
    return 0


def et_item(base: dict) -> dict:
    return {**base, "team_A_name": "无锡队", "team_B_name": "徐州队", "minute_period": "AET", "fs_A": "1", "fs_B": "1", "start_play": "2026-10-11 11:40:00", "end_play": "2026-10-11 14:20:00"}


def main() -> int:
    parser = argparse.ArgumentParser(description="更新苏超淘汰赛完赛比分")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    setup_log()
    if args.self_test:
        return self_test()
    lock = acquire_lock()
    if lock is None and LOCK_PATH.exists():
        return 0
    try:
        return run(args.dry_run, args.fixture)
    except Exception as exc:
        log.exception("更新失败：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
