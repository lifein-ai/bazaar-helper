# BazaarHelper

> Current direction: the browser Web UI is deprecated. BazaarHelper runs as a
> local API service for the in-game BepInEx overlay. Keep `/api/*` endpoints and
> recommendation logic maintained; do not add new browser-page features.

BazaarHelper 是一个面向《The Bazaar》的本地决策辅助工具。它读取游戏实时状态、卡牌数据、事件数据和社区阵容配置，分析当前事件、商店和可见卡牌的收益，并在本地 Web UI 中给出可解释的选择建议。

项目目标不是替玩家做唯一答案，而是帮助玩家快速判断：

- 当前事件或商店可能产出哪些卡牌
- 哪些选项命中当前英雄和当前阵容
- 核心卡、过渡卡、可选卡的命中情况
- 当前金币、血量、声望、背包等状态下的购买/刷新价值
- 当前阵容包含哪些关键卡
- 是否值得调用 AI 做中文策略解释

## 工作流程

```text
The Bazaar 游戏状态
-> BepInEx 插件导出 runtime/game_state.json
-> Python 读取 data/ 数据库
-> 自动匹配当前英雄阵容
-> 分析事件、商店、可见卡牌收益
-> Web UI 展示推荐
-> 可选 DeepSeek AI 中文分析
```

## 主要功能

- 实时读取 `runtime/game_state.json`
- 根据当前英雄只显示对应英雄的阵容
- 支持手动选择阵容，也支持按已有卡牌自动匹配阵容
- 点击左侧“阵容目标”可展开查看阵容包含卡牌
- 分析当前事件、商店和奖励选项
- 显示核心卡命中率、相关卡命中率和推荐等级
- 展示已拥有物品、已拥有技能、当前可见卡
- 对未知事件生成提示，方便后续补数据
- 可选调用 DeepSeek 生成中文策略分析
- 提供 BepInEx 插件，用于把游戏局内状态导出给 Python 工具

## 目录结构

```text
.
├─ src/                         Python 主程序和推荐逻辑
├─ data/                        程序实际读取的数据
├─ scripts/                     数据导入、转换和审计脚本
├─ bepinex/BazaarStateExporter/ BepInEx 状态导出插件
├─ docs/                        项目文档和用户指南
├─ examples/                    示例状态文件
├─ tests/                       回归测试
├─ runtime/                     运行时状态文件，不提交 Git
├─ outputs/                     输出文件，不提交 Git
├─ release/                     打包后的发布目录
├─ start.bat                    发布版启动脚本
├─ start_ui.ps1                 开发环境 UI 启动脚本
├─ package_release.ps1          发布打包脚本
└─ VERSION                      当前版本号
```

## 核心模块

- `src/web_app.py`  
  本地 Web UI 和 HTTP API。负责读取实时状态、归一化数据、调用推荐逻辑、返回页面和 JSON API。

- `src/main.py`  
  命令行入口。适合开发调试、手动传入 hero/build/day/events 进行分析。

- `src/game_state.py`  
  定义当前局内状态 `GameState`，包括英雄、天数、事件选项、已拥有卡牌、金币、血量、商店状态等。

- `src/advisor.py`  
  把 `GameState` 转换为多事件推荐结果，并按推荐价值排序。

- `src/recommender.py`  
  核心推荐逻辑。负责卡池推断、稀有度过滤、阵容定位、命中率、升级收益、资源收益和推荐理由。

- `src/stage_build_matcher.py`  
  分析当前阶段、当前商店和候选卡牌对不同阵容的命中关系。

- `src/data_loader.py`  
  加载并合并卡牌、事件、阵容、评分、翻译和稀有度规则。

- `src/ai_advisor.py`  
  把推荐结果压缩成 AI 输入，并调用 DeepSeek 生成自然语言分析。

## 数据文件

主要数据位于 `data/`：

- `cards_generated.json`：从游戏 cache 导入的卡牌基础数据
- `skills_generated.json`：技能数据
- `events.json`：事件、商店、奖励规则
- `event_overrides.json`：人工修正事件规则
- `community_builds.json`：社区阵容配置
- `card_ratings.json`：卡牌评分和补充定位
- `rarity_rules.json`：稀有度规则
- `translations_zh_cn.json`：中文翻译

推荐逻辑的数据优先级大致是：

```text
community_builds.json
> card_ratings.json
> events.json
> cards_generated.json
```

## 快速启动

推荐使用发布版目录中的：

```text
start.bat
```

开发环境可以运行：

```powershell
.\start_ui.ps1
```

或直接启动 Python Web UI：

```powershell
python src/web_app.py
```

默认地址：

```text
http://127.0.0.1:8765
```

### macOS

macOS 使用源码版服务和已安装的 BepInEx。需要 Python 3.10+；首次运行时会自动通过 Homebrew 安装 .NET 8 SDK：

```bash
cd /Users/你的用户名/Documents/bazaarhelper
./start_macos.sh
```

脚本会构建并安装 `BazaarStateExporter.dll`、启动本地 API，但不会打开游戏。
首次启动后，插件状态会写入：

```text
~/.bazaar_helper/runtime/game_state.json
```

若希望由脚本通过 `run_bepinex.sh` 打开游戏：

```bash
./start_macos.sh --launch-game
```

若 8765 被其他程序占用，启动器会自动选择下一个可用本地端口并同步更新
插件配置；若该端口是失活的 BazaarHelper，则会先重启它，而不是继续换端口。
也可以显式指定端口：

```bash
BAZAAR_HELPER_PORT=8766 ./start_macos.sh
```

## 命令行示例

手动传入英雄、阵容、天数和事件：

```powershell
python src/main.py --hero Vanessa --build VanessaAquaticAmmo --day 6 --events Colt Kina Gaseo
```

读取完整状态 JSON：

```powershell
python src/main.py --state-json examples/game_state.example.json --top 3
```

预览将发送给 AI 的摘要，不实际调用 API：

```powershell
python src/main.py --state-json examples/game_state.example.json --ai-dry-run
```

调用 DeepSeek：

```powershell
python src/main.py --state-json examples/game_state.example.json --ai
```

## Web UI 说明

Web UI 会优先读取：

```text
runtime/game_state.json
```

如果文件不存在、过期或仍是插件占位状态，页面会提示需要启动游戏和状态导出插件。

左侧区域显示：

- 英雄
- 天数
- 金币
- 生命
- 声望
- 收入
- 当前阵容目标
- 当前事件
- 已拥有物品
- 已拥有技能
- 当前可见卡

阵容选择规则：

- 下拉框只显示当前英雄可用阵容
- 可以选择“自动匹配已有卡牌”
- 如果浏览器缓存了其他英雄的旧阵容，会自动清空
- 即使前端传入其他英雄的阵容，后端也会忽略并重新匹配

阵容详情：

- 点击左侧“阵容目标”卡片可展开
- 展示核心卡、过渡卡、可选卡和需求标签
- 卡牌名会尽量使用中文翻译

常用接口：

```text
GET  /
GET  /api/state
GET  /api/options
GET  /api/options?hero=Vanessa
GET  /api/analysis
POST /api/state
```

## BepInEx 插件

插件位于：

```text
bepinex/BazaarStateExporter/
```

插件职责很简单：只读取游戏事实并持续写入结构化状态，不做推荐，也不调用 AI。

默认输出路径：

```text
%LOCALAPPDATA%\BazaarHelper\runtime\game_state.json
```

最小可用状态示例：

```json
{
  "source": "bepinex",
  "hero": "Vanessa",
  "day": 6,
  "event_options": ["Colt", "Kina", "Gaseo"]
}
```

增强字段示例：

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

更多插件说明见：

```text
docs/bepinex_integration.md
bepinex/BazaarStateExporter/README.md
```

## DeepSeek AI 分析

AI 分析是可选功能。规则系统会先完成事件过滤、卡池推断、概率计算和推荐排序，AI 只读取压缩后的推荐摘要，用于生成中文解释和策略建议。

推荐把 API Key 写入本地运行时文件：

```text
runtime/deepseek_api_key.txt
```

文件内容只放一行 DeepSeek API Key。该文件不应提交到 Git。

也可以使用环境变量：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

默认模型：

```text
deepseek-chat
```

默认 API 地址：

```text
https://api.deepseek.com
```

## 数据维护

常用脚本：

- `scripts/import_live_cards.py`：导入当前游戏 cache 中的卡牌数据
- `scripts/import_game_cache.py`：从官方 cache 导入卡牌、技能、遭遇等数据
- `scripts/build_events_from_encounters.py`：从 encounter 数据生成 `data/events.json`
- `scripts/audit_event_rules.py`：审计事件规则质量
- `scripts/audit_event_pool.py`：审计事件卡池
- `scripts/import_zh_translations.py`：导入中文翻译

事件数据通常包括：

- `shops`：商店
- `skill_shops`：技能商店/技能教学
- `item_rewards`：物品奖励
- `item_events`：强化已有物品
- `resource_events`：金币、经验、生命、收入等资源
- `enchant_events`：附魔事件
- `combat_events`：战斗事件
- `unknown_events`：暂未结构化的事件

## 测试

运行全部测试：

```powershell
python -m pytest -q
```

常用快速检查：

```powershell
python -m py_compile src/web_app.py src/recommender.py src/ai_advisor.py
python -m pytest -q tests/test_web_app.py tests/test_recommender.py
```

## 发布打包

发布脚本：

```powershell
.\package_release.ps1
```

脚本会执行：

1. 运行发布前测试
2. 构建 BepInEx 插件
3. 使用 PyInstaller 构建 `BazaarHelper.exe`
4. 生成用户指南
5. 组装 `release/BazaarHelper`
6. 校验发布文件完整性

发布目录通常包含：

- `BazaarHelper.exe`
- `_internal/`
- `data/`
- `examples/`
- `start.bat`
- `install_plugin.bat`
- `set_ai_key.bat`
- `update_helper.ps1`
- `VERSION`
- `version.json`
- `BazaarHelper_User_Guide.docx`
- `bepinex_plugin/BazaarStateExporter.dll`

## 设计原则

- 稳定性优先于自动化程度
- 结构化输入优先于 OCR
- 规则系统先给出可解释推荐
- AI 只做解释和策略补充，不替代规则判断
- BepInEx 插件只负责导出事实，不耦合推荐知识库
- 阵容知识维护在 `data/community_builds.json`
