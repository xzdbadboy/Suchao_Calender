# CLAUDE.md

本文件为Claude Code（claude.ai/code）在此仓库中工作提供指导。

## 项目概述

**苏超日历项目（SuCha Calendar 2026）**

2026年江苏省城市足球联赛（苏超）的日历管理项目。项目生成ICS日历文件，兼容所有主流日历应用（苹果日历、Google日历、Outlook等）。

### 核心产物

1. **`SuCha2026Schedule.ics`** - 日历文件，包含78场常规赛
   - 完整的2026赛季赛程（22轮）
   - 第1-9轮（30场比赛）已标注最终比分
   - 每场比赛通过ATTACH属性引用官方球队队标
   - 时间格式：UTC ISO 8601
   - 可导入任何标准日历应用

2. **`icon/` 文件夹** - 球队队标和设计文档
   - 13张官方球队队标PNG图片（80-150KB每张）
   - 13份球队设计语言文档（TXT格式）
   - README.md说明文件

## 项目结构

```
Suchao_Calender/
├── SuCha2026Schedule.ics       # 主日历文件（ICS格式）
├── 需求文件.md                  # 原始需求文档
├── icon/                        # 球队队标和文档
│   ├── README.md               # icon文件夹说明
│   ├── [城市].png              # 官方球队队标（13支）
│   └── [城市].txt              # 球队设计语言（13支）
└── .obsidian/                  # Obsidian库配置
```

## 球队信息

**13支参赛球队及其资源位置：**

| 城市 | 队标文件 | 设计文档 | 核心元素 |
|------|--------|--------|--------|
| 南京 | nanjing.png | nanjing.txt | 辟邪神兽 |
| 无锡 | wuxi.png | wuxi.txt | 玉飞凤 |
| 徐州 | xuzhou.png | xuzhou.txt | 盾牌 |
| 常州 | changzhou.png | changzhou.txt | 火龙 |
| 苏州 | suzhou.png | suzhou.txt | 甪端神兽 |
| 南通 | nantong.png | nantong.txt | 狼头 |
| 连云港 | lianyungang.png | lianyungang.txt | 船锚 |
| 淮安 | huaian.png | huaian.txt | 骏马 |
| 盐城 | yancheng.png | yancheng.txt | 麋鹿 |
| 扬州 | yangzhou.png | yangzhou.txt | 雄狮 |
| 镇江 | zhenjiang.png | zhenjiang.txt | ZJ字母 |
| 泰州 | taizhou.png | taizhou.txt | 三水纹样 |
| 宿迁 | suqian.png | suqian.txt | 项羽形象 |

## 项目规范

### 语言约定

本项目的编辑、说明、注释等**全部使用中文**。唯一例外是代码示范和技术示例，必要时可使用英文来展示代码片段或配置。

**应用范围：**
- ✅ 代码注释、文档说明 → **中文**
- ✅ 提交信息、变更日志 → **中文**
- ✅ 配置文件中的说明 → **中文**
- ⚙️ 代码示范（ICS格式等） → **英文**（仅限技术示例）

**示例：**
```
✅ 正确：
# 这是一个赛事块，包含比赛信息
BEGIN:VEVENT
SUMMARY:苏超第1轮-常州队 3:0 南通队

❌ 避免：
# This is an event block for match information
# 这是一个赛事块...
```

### 安全与隐私

**严格禁止上传以下内容到公网：**
- 🔐 API密钥、访问令牌、密码
- 🍪 Cookie、Session令牌、身份验证凭证
- 🔑 加密密钥、私钥文件
- 📧 个人邮箱、电话等隐私信息
- 💳 银行卡号、支付信息
- 🆔 身份证号、个人ID等敏感数据
- 🔗 内部服务URL、私有API端点

**处理敏感信息的正确方式：**
1. 使用环境变量（.env文件），**不上传.env文件**
2. 使用`.gitignore`排除敏感文件
3. 配置文件中使用占位符：`API_KEY=your_key_here`（示例用途）
4. 使用密钥管理系统（如1Password、LastPass等）存储真实凭证

**提交前检查清单：**
- ✅ 确认没有硬编码的密钥或密码
- ✅ 确认没有包含个人隐私信息的日志
- ✅ 确认敏感配置文件已被.gitignore排除
- ✅ 使用`git diff --cached`检查暂存内容

## ICS文件格式

### 赛事结构

日历中每场比赛包含：
- **SUMMARY**：赛事名、轮次、球队名（标题中不含比分）
- **DTSTART/DTEND**：比赛时间（UTC格式，通常19:00-21:00）
- **ATTACH**：球队队标PNG文件引用（本地路径）
- **DESCRIPTION**：比赛详情，已结束的比赛包含最终比分

### 示例赛事

```
BEGIN:VEVENT
SUMMARY:苏超第1轮-常州队 3:0 南通队
DTSTART:20260411T190000Z
DTEND:20260411T210000Z
ATTACH;FMTTYPE=image/png:file:///Volumes/Personal/Suchao_Calender/icon/changzhou.png
ATTACH;FMTTYPE=image/png:file:///Volumes/Personal/Suchao_Calender/icon/nantong.png
DESCRIPTION:阶段: 常规赛\n轮次: 第1轮\n比分: 常州队 3:0 南通队\n更新时间: 2026/06/19
END:VEVENT
```

## 关键工作流程

### 更新比赛比分

1. 日历中的比赛日期和时间已固定（4月11日-9月19日）
2. 比赛完成后，在SUMMARY和DESCRIPTION字段中添加比分
3. 格式：`球队1 进球数1:进球数2 球队2`（如"常州队 3:0 南通队"）
4. 在DESCRIPTION中添加：`比分: 球队1 进球数1:进球数2 球队2`
5. 保持ATTACH属性指向正确的球队队标

### 添加后续轮次

日历已包含全部22轮常规赛。如需添加淘汰赛等新阶段：
1. 确定淘汰赛日期和对阵情况
2. 添加新的VEVENT块，设置正确的DTSTART/DTEND
3. 通过ATTACH引用对应球队的队标
4. SUMMARY格式与常规赛保持一致

### 队标文件引用

- 所有ATTACH路径使用绝对路径：`file:///Volumes/Personal/Suchao_Calender/icon/[城市].png`
- 项目移动后需更新这些路径（或改为相对路径，取决于日历应用支持）
- 每场赛事应有恰好2个ATTACH块（每队一个）

## 数据来源

- **官方队标**：jscl.moyu.js.cn（苏超官方网站）
- **设计语言**：腾讯新闻（2026年3月17日）
- **赛程数据**：多个本地来源（更新至2026年6月19日）

## 当前状态（截至2026-06-19）

- ✅ 全部78场常规赛已排期
- ✅ 第1-9轮（30场）包含最终比分
- ⏳ 第10-22轮等待比赛结果
- ✅ 全部13支球队队标已下载存储
- ✅ 设计语言文档已完成
- 🔄 淘汰赛赛程待定

## 后续工作注意事项

### 日历应用兼容性

ICS中的ATTACH属性使用本地文件路径，大多数应用能支持，但不同系统可能需要调整路径。导入目标日历应用前应进行测试。

### 时区处理

所有时间使用UTC格式（T后缀为Z）。验证在本地时区中的显示时间是否正确。

### 球队队标更新

如果联赛更新官方队标，替换icon文件夹中的PNG文件即可。ICS文件会自动引用最新的队标。

### Obsidian集成

项目使用Obsidian库格式（存在.obsidian配置文件夹）。编辑时保持此结构完整，用于知识管理。

### 中文字符支持

文件名和内容使用UTF-8编码，包含中文字符。编辑文件时保持编码格式。

### 赛程准确性

数据截至2026年6月19日。进行重大更新前，应核实官方联赛来源。
