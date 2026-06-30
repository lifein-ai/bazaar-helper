# The Bazaar AI 助手

这是一个用于《The Bazaar》的 Python 决策辅助项目。它读取官方 cache JSON、事件数据、阵容配置和当前局面，分析商店/事件收益，并输出可解释的推荐。

核心流程：

```text
结构化游戏状态 -> 匹配适用阵容 -> 过滤候选卡池 -> 计算概率 -> 输出事件/卡牌推荐 -> 可选 AI 策略分析
```

## 设计原则

主流程不依赖 OCR。核心输入应来自：

- 官方 cache JSON
- 手动结构化 JSON
- 未来 BepInEx 插件导出的 game state JSON

优先级：

```text
稳定性 > 准确性 > 自动化程度
```

AI 分析不是规则系统的替代品。规则系统先负责卡池过滤、概率计算和基础推荐；AI 只读取精简后的推荐摘要，用来做中文解释、对比和下一步策略建议。

## 目录结构

```text
.
├── data/                  # 程序实际读取的数据
├── scripts/               # 数据导入脚本
├── src/                   # 核心代码
├── docs/                  # 架构和数据维护说明
├── examples/              # 示例输入
└── tests/                 # 回归测试
```

## 核心模块

- `src/data_loader.py`：加载并合并卡牌、评分、事件和阵容数据
- `src/game_state.py`：定义当前游戏状态 `GameState`
- `src/build_strategy.py`：判断当前游戏时期，以及 build 是否适用于当前时期
- `src/advisor.py`：把 `GameState` 转成推荐结果
- `src/recommender.py`：卡池推断、概率计算、推荐解释
- `src/ai_advisor.py`：把推荐结果压缩成 AI 摘要，并可调用 DeepSeek 分析
- `src/main.py`：命令行入口
- `scripts/import_game_cache.py`：从官方 cache 导入卡牌、技能、遭遇等数据
- `scripts/build_events_from_encounters.py`：从官方 encounter 数据生成 `data/events.json`

## 快速运行

```bash
python src/main.py --hero Vanessa --build VanessaAquaticAmmo --day 6 --events Colt Kina Gaseo
```

读取结构化状态：

```bash
python src/main.py --state-json examples/game_state.example.json --top 1
```

## DeepSeek AI 分析

先设置 API Key。PowerShell 示例：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

如果使用 Web UI，更推荐写入本地密钥文件，避免后台进程读不到环境变量：

```text
runtime/deepseek_api_key.txt
```

文件内容只放一行 DeepSeek API Key。这个文件已加入 `.gitignore`，不会被提交。

建议先 dry run，查看会发给 AI 的精简摘要：

```bash
python src/main.py --hero Vanessa --build VanessaAquaticAmmo --day 6 --events Colt Kina Gaseo --ai-dry-run
```

确认摘要合理后再调用 DeepSeek：

```bash
python src/main.py --hero Vanessa --build VanessaAquaticAmmo --day 6 --events Colt Kina Gaseo --ai
```

也可以和状态 JSON 一起使用：

```bash
python src/main.py --state-json examples/game_state.example.json --top 3 --ai
```

默认模型是 `deepseek-chat`，默认 API 地址是 `https://api.deepseek.com`。如果之后要切模型：

```bash
python src/main.py --state-json examples/game_state.example.json --ai --ai-model deepseek-chat
```

## 本地 UI

推荐启动方式：

```text
双击 start_ui.bat
```

或在 PowerShell 中运行：

```powershell
.\start_ui.ps1
```

脚本会自动停止旧 UI、启动新 UI，并打开浏览器。

手动启动方式：

```bash
python src/web_app.py
```

默认地址：

```text
http://127.0.0.1:8765
```

UI 会优先读取：

```text
runtime/game_state.json
```

如果这个文件不存在，则读取：

```text
实时状态不存在时页面会直接提示启动游戏和状态导出插件，不再回退到示例数据。
```

页面支持：

- 查看当前 hero、day、build、stage
- 展示事件推荐、命中概率、核心概率、关键卡
- 编辑并保存运行时 JSON
- 点击按钮调用 DeepSeek AI 分析

## BepInEx 输出协议

BepInEx 插件不需要做推荐，也不需要调用 AI。它只需要尽量读取游戏事实，并持续写入 `runtime/game_state.json`。

当前插件骨架在 `bepinex/BazaarStateExporter/`，接入步骤见 `docs/bepinex_integration.md`。

`build` 不属于 BepInEx 输出字段。阵容推荐知识只维护在 `data/community_builds.json`，UI 会根据当前 hero/day 自动选择，也可以手动切换。

最低可用字段：

```json
{
  "source": "bepinex",
  "hero": "Vanessa",
  "day": 6,
  "event_options": ["Colt", "Kina", "Gaseo"]
}
```

BepInEx 不需要知道当前使用哪个 build。即使运行时 JSON 里误带了 `build`，Web UI 分析也会忽略它，避免插件层和推荐知识库耦合。

增强字段：

```json
{
  "owned_cards": [
    {"name": "Ballista", "rarity": "gold", "enchantments": ["Fiery"]}
  ],
  "visible_cards": ["Ballista", "Cannon", "Dive Weights"],
  "gold": 12,
  "health": 43
}
```

缺少增强字段时系统会降级：

- 没有 `owned_cards`：不计算升级收益
- 没有 `visible_cards`：只分析事件，不分析具体购买
- 没有 `gold`：不判断购买能力
- 没有 `health`：不判断保命优先级

附魔会改变运行时卡牌 tag。例如 `Fiery` 会让已拥有卡牌额外视为 `burn` 物品，因此 `Improve your Burn items` 这类事件会命中它。当前支持的常见映射包括：

```text
Fiery/Burn -> burn
Toxic/Poison -> poison
Icy/Freeze -> freeze
Shielded/Shield -> shield
Restorative/Heal -> heal
Turbo/Haste -> haste
Deadly/Crit -> crit
Heavy/Obsidian -> damage
```

## 事件数据覆盖

`scripts/build_events_from_encounters.py` 会从官方 encounter 数据生成多类事件：

```text
shops             商店
skill_shops       技能商店/技能教学
item_rewards      获得物品
item_events       强化已有物品
resource_events   金币、XP、生命、收入等资源
enchant_events    附魔事件
combat_events     战斗事件
unknown_events    有描述但暂未结构化的事件
```

如果 BepInEx 读到非商店事件，也直接写进 `event_options` 即可。

## 结构化状态示例

```json
{
  "source": "plugin",
  "hero": "Vanessa",
  "build": "VanessaAquaticAmmo",
  "day": 6,
  "event_options": ["Colt", "Kina", "Gaseo"],
  "owned_cards": [
    {"name": "Ballista", "rarity": "gold"}
  ],
  "visible_cards": [],
  "gold": 12,
  "health": 43
}
```

## Build 配置方式

`data/community_builds.json` 中的每个 build 代表一个可切换的社区阵容路线。每个 build 独立标注适用时期。

示例：

```json
{
  "VanessaAquaticAmmo": {
    "hero": "Vanessa",
    "display_name": "Vanessa Aquatic Ammo",
    "applicable_stages": ["mid", "late"],
    "day_range": [5, null],
    "build_summary": "阵容描述",
    "match_notes": ["什么时候考虑转入这个阵容"],
    "core_cards": [],
    "transition_cards": [],
    "optional_cards": [],
    "wanted_tags": [],
    "event_priorities": [],
    "avoid_events": []
  }
}
```

游戏时期默认规则：

```text
early: Day 1-4
mid:   Day 5-8
late:  Day 9+
```

后续 AI 会根据当前天数、已有卡牌和各 build 的适用时期，先判断当前更适合哪个阵容，再推荐事件选择。

## 数据导入

导入官方 cache：

```bash
python scripts/import_game_cache.py --check-only
python scripts/import_game_cache.py
```

从官方 encounter 生成事件数据：

```bash
python scripts/build_events_from_encounters.py --check-only
python scripts/build_events_from_encounters.py
```

普通商店默认使用当前 hero + Common/Neutral 卡池。只有官方描述包含 `from any Hero` 的商店才使用全英雄卡池。

## 测试

```bash
python -m unittest discover -s tests
```

## 下一步方向

- 补充更多 build，并为每个 build 标注适用时期和核心卡
- 根据当前已拥有卡牌自动匹配最适合的 build
- 加入当前可见卡牌的购买建议
- 输出 JSON 格式推荐结果，方便未来 UI 或插件接入
