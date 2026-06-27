# PROJECT_CONTEXT_FOR_CHATGPT

## 1. 项目一句话

这是一个本地运行的《The Bazaar》AI 决策助手项目：通过 BepInEx 插件导出结构化游戏状态，Python Web UI 读取状态并展示事件推荐，规则系统先计算事件/商店收益，DeepSeek 只负责把结构化结果解释成中文短建议。

核心原则：

- 主输入是结构化状态，不依赖 OCR。
- 规则系统负责事实计算，AI 负责解释，AI 不能编造事件、卡牌、概率或规则。
- `data/events.json` 是基础事件库；人工修正和新增优先写 `data/event_overrides.json`。
- 尽量做最小改动，不要随便重构架构。

---

## 2. 当前目录结构重点

常用目录：

```text
bepinex/BazaarStateExporter/    BepInEx 插件项目
data/                           正式数据
docs/                           项目说明
examples/                       示例状态
raw_data/                       原始 CSV
scripts/                        数据导入/转换脚本
src/                            Python 主逻辑
tests/                          测试
runtime/                        运行时状态，不应提交 Git
```

关键文件：

```text
src/web_app.py                  本地 Web UI、状态归一化、缺失事件记录、AI 分析入口
src/recommender.py              推荐核心：卡池、概率、收益、推荐理由
src/data_loader.py              数据加载、事件展平、人工覆盖合并
src/ai_advisor.py               DeepSeek 输入压缩与调用
src/advisor.py                  多事件分析编排
src/game_state.py               当前游戏状态模型
src/build_strategy.py           游戏阶段与 Build 适用性
data/events.json                基础事件库
data/event_overrides.json       人工事件修正层
data/cards_generated.json       官方卡牌转换数据
data/card_ratings.json          卡牌评级和旧 Build 定位补充
data/builds.json                Build 主数据
data/rarity_rules.json          天数/品质规则
data/translations_zh_cn.json    中文显示映射
runtime/game_state.json         插件实时状态输出
runtime/missing_events.json     缺失事件记录
runtime/observed_event_graph.json 父子事件观察图
```

---

## 3. 输入架构原则

项目已决定不把 OCR 作为核心输入。

优先级：

```text
1. BepInEx / Unity 插件导出的结构化状态
2. 官方 cache JSON 或其他官方数据
3. 手动结构化输入
4. OCR 仅作为辅助实验
```

原因：

- 推荐系统需要 card id、internal name、tags、hero、rarity、event pool rule、owned cards、day、build roles。
- 这些信息大多不稳定显示在游戏 UI 文本里。
- OCR 容易误识别，污染后续推荐结果。
- 推荐核心 `recommender.py` 不应该知道 OCR 的存在。

---

## 4. 数据加载与人工覆盖规则

`src/data_loader.py` 当前加载流程：

```text
cards_generated.json
+ card_ratings.json
→ 合并卡牌基础信息和评级

events.json
→ flatten_events_list()
→ 得到按事件名索引的 events

event_overrides.json
→ apply_event_overrides()
→ 覆盖/新增人工事件修正

builds.json
rarity_rules.json
translations_zh_cn.json
→ 一并返回给 Web UI 和推荐器
```

`event_overrides.json` 的格式是：

```json
{
  "事件名": {
    "_override_reason": "为什么修正",
    "要修改的字段": "新值"
  }
}
```

合并规则：

- `dict`：递归深度合并。
- `list`：整体替换，不是追加。
- 普通字段：override 覆盖 base。
- 新增事件时必须写 `name`、`source_id`、`source_ids`、`event_category` 等基础字段。
- 已有事件可以只写需要修改的字段。

示例：修改 Midsworth 不卖中型物品：

```json
{
  "Midsworth": {
    "_override_reason": "人工修正：Midsworth 不出售中型物品。",
    "shop_pool": {
      "size_filter": ["small", "large"]
    }
  }
}
```

示例：新增未知事件：

```json
{
  "新事件名": {
    "_override_reason": "人工新增：events.json 中缺失，先按未知事件处理。",
    "name": "新事件名",
    "source_id": "source_id",
    "source_ids": ["source_id"],
    "event_heroes": ["Common"],
    "event_type": "unknown_event",
    "event_category": "unknown_events",
    "resource_rewards": {
      "gold": 0,
      "exp": 0,
      "health": 0
    },
    "notes": "人工补充：奖励规则待测试。"
  }
}
```

---

## 5. 事件系统结构

### shops

商店事件，用 `shop_pool` 计算卡池。

常见结构：

```json
"shop_pool": {
  "reward_tags": ["weapon"],
  "match_mode": "any",
  "rarity_filter": null,
  "rarity_rule": "normal_shop_by_day",
  "excluded_tags": ["legendary"],
  "hero_scope": "current"
}
```

常用字段：

```text
reward_tags     标签筛选，如 weapon / aquatic / ammo
match_mode      any / all
rarity_filter   固定品质范围
rarity_rule     按天数变化的品质规则
excluded_tags   排除标签，通常排除 legendary
hero_scope      current / any / fixed
hero_filter     固定英雄，如 Vanessa
size_filter     small / medium / large
exact_names     固定卡名
```

### item_rewards

获得物品事件，用 `card_reward` 计算卡池。

```json
"card_reward": {
  "enabled": true,
  "exact_names": [],
  "reward_tags": [],
  "match_mode": "any",
  "rarity_filter": null,
  "rarity_rule": "normal_shop_by_day",
  "excluded_tags": ["legendary"],
  "hero_scope": "current"
}
```

`card_reward.count` 已接入 `src/recommender.py`：

- 没有 `count`：默认 1。
- `count: 2`：按获得 2 个物品计算。
- 非法值/空值：回退 1。
- 商店/技能商店仍默认 `SHOP_CARD_COUNT = 6`。

示例：获得两个当前英雄物品：

```json
{
  "事件名": {
    "card_reward": {
      "count": 2
    },
    "notes": "Get 2 items."
  }
}
```

### resource_events

资源事件，用 `resource_rewards`。

```json
"resource_rewards": {
  "gold": 1,
  "exp": 1,
  "health": 0
}
```

常见资源字段：

```text
gold
exp
health
max_health
income
regen
healthregen
```

如果数值随等级变化、不想显示具体数字，可以用：

```json
{
  "resource_rewards": {},
  "qualitative_rewards": ["regen"],
  "_dynamic_reward": true,
  "notes": "获得再生，数值等于你的等级。"
}
```

### item_events

作用于已有物品的事件，例如升级、强化、转化。

```json
{
  "event_type": "item_event",
  "effect": "upgrade_items",
  "target_tags": ["weapon"],
  "match_mode": "any"
}
```

### enchant_events

附魔事件。

```json
{
  "event_type": "enchant_event",
  "effect": "enchant_items",
  "target_tags": ["weapon"],
  "enchantment_tags": ["crit"],
  "match_mode": "any"
}
```

### unknown_events

只知道事件存在，但不知道收益规则时使用。

原则：

- 不确定就先放 unknown。
- 不要乱写 `card_reward`。
- 错误卡池比缺失数据更危险。

---

## 6. 推荐器当前逻辑

`src/recommender.py` 负责：

- 根据事件规则推断候选卡池。
- 根据标签、品质、英雄池、尺寸、固定卡名筛选卡牌。
- 判断卡牌在当前 Build 中是 core / transition / optional / unrelated。
- 计算相关卡数量、核心卡数量、S/A 卡数量。
- 计算至少命中相关卡/核心卡概率。
- 分析已有卡牌是否可升级。
- 分析资源收益、已有物品命中、附魔/升级收益。
- 分析 `followup_options`。
- 输出推荐等级和理由。

推荐等级：

```text
High Value
Medium Value
Low Value
```

`get_event_draw_count()` 当前规则：

```text
shops / skill_shops → SHOP_CARD_COUNT = 6
item_rewards → 默认 1
card_reward.count 存在且合法 → 使用 count
count 缺失 / None / 非法 → 回退 1
```

注意：`analyze_event()` 里可能仍残留无用变量 `reward_count`，如果存在可以清理，但不影响当前 draw_count 生效。

---

## 7. Web UI 与运行时状态

`src/web_app.py` 负责：

- 启动本地 Web UI。
- 读取 `runtime/game_state.json`。
- 没有实时状态时读取 `examples/game_state.example.json`。
- 归一化事件选项和卡牌条目。
- 自动匹配 Build。
- 调用 `advisor.py` 得到规则推荐。
- 调用 `ai_advisor.py` 得到 AI 分析。
- 记录缺失事件到 `runtime/missing_events.json`。
- 维护父子事件观察图 `runtime/observed_event_graph.json`。

常用 API：

```text
GET  /
GET  /api/state
GET  /api/options
GET  /api/analysis
POST /api/state
```

当前 AI 分析入口是：

```text
include_ai and response["recommendations"]
```

因此 AI 不应该因为 `warnings` 存在就直接停止。缺失数据应该降低置信度，而不是完全阻断 AI。

---

## 8. PVP / 怪物 / 战斗过滤

目标：

- PVP 不参与推荐。
- 怪物战斗不参与推荐。
- CombatEncounter 不应该写入 `missing_events.json`。
- 普通 EventEncounter 仍然保留。

过滤原则：

```text
id 以 ste_ 开头 → 事件内部步骤，不分析
id 以 com_ 开头 → 战斗/怪物，不分析
id 以 pvp_ 开头 → PVP，不分析
kind 是 step / combat / pvp → 不分析
card_type 包含 combat / pvp → 不分析
card_type 是 EventEncounter → 可以分析
```

注意：不能只看 `kind`。实测可能出现：

```json
{
  "id": "com_xxx",
  "kind": "encounter",
  "card_type": "CombatEncounter"
}
```

因此必须优先看 `id` 和 `card_type`。

当前 `src/web_app.py` 已在 `is_detailed_encounter_option()`、`detailed_option_kind()`、`auto_observe_event_graph()` 中做了相关过滤和容错。

---

## 9. observed_event_graph 容错

`runtime/observed_event_graph.json` 是运行时观察图，可能被坏数据污染。

典型错误：

```text
'NoneType' object does not support item assignment
```

常见原因：

- graph 节点不是 dict。
- `children` 是 null。
- child 不是 dict。
- parent_record 是 None。
- 旧文件里存在坏结构。

当前 `web_app.py` 已有防御式处理：

- `load_observed_event_graph()` 会清洗节点。
- `_coerce_observed_graph_node()` 会修正 `parent_source_ids`、`children`、`observed_count`。
- `write_observed_event_graph()` 写入前会清洗。
- `analyze_payload()` 调用 `auto_observe_event_graph()` 时有 try/except，观察图失败不应影响主分析。

如果仍然异常，可先清空：

```text
runtime/observed_event_graph.json
```

内容改为：

```json
{}
```

---

## 10. 缺失事件流程

缺失事件记录位置：

```text
runtime/missing_events.json
```

处理流程：

```text
1. 查看缺失事件 name 和 raw_event_options_detailed
2. 判断是否是普通事件，还是 combat / pvp / step 误记录
3. 如果是 combat / pvp / step，不要补事件，应该修过滤逻辑并清掉旧记录
4. 如果是真缺失事件，优先写入 data/event_overrides.json
5. 不知道收益就写 unknown_event
6. 确认收益后再补 shop_pool / card_reward / resource_rewards
7. 保存后重启 UI
```

---

## 11. AI 分析层

`src/ai_advisor.py` 负责：

- 把推荐器结果压缩成中文结构化 payload。
- 调 DeepSeek。
- 清理 Markdown 符号。
- 输出短建议。

当前要求：

- 必须中文。
- 不输出 Markdown。
- 总字数控制短。
- 只能基于输入数据判断。
- 不得编造卡牌、事件、概率或规则。

API Key 支持：

```text
环境变量 DEEPSEEK_API_KEY
runtime/deepseek_api_key.txt
```

`runtime/deepseek_api_key.txt` 不应提交 Git。

---

## 12. 当前已知代码维护点

### web_app.py 曾经过多次热修，可能存在重复函数

重点检查：

```text
enrich_child_from_official_cards
event_name_from_source_id
load_observed_event_graph
recommendation_label
role_label
```

Python 会以后定义覆盖先定义，所以短期未必会炸，但会造成维护混乱。  
后续应做一次最小清理：只保留最后版本/更安全版本，不改变逻辑，不重构架构。

### recommender.py 可能存在废变量

如果 `analyze_event()` 中仍有：

```python
card_reward = event_data.get("card_reward", {})
reward_count = int(card_reward.get("count", 1)) if isinstance(card_reward, dict) else 1
```

而后面已经使用：

```python
draw_count = get_event_draw_count(event_data)
```

则前两行可以删除。

---

## 13. 运行方式

启动 Web UI：

```powershell
.\start_ui.ps1
```

或：

```powershell
python src\web_app.py
```

命令行测试：

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie
```

AI dry run：

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie --ai-dry-run
```

---

## 14. 测试建议

每次改完优先跑：

```powershell
python -m pytest
```

当前项目已有：

```text
tests/test_recommender.py
tests/test_web_app.py
```

如果只是快速手测：

```powershell
python src\web_app.py --port 8765
```

然后打开：

```text
http://127.0.0.1:8765
```

---

## 15. Git 与敏感文件

不要提交：

```text
.venv/
.venv_old/
__pycache__/
runtime/
outputs/
tmp/
.edge-bazaardb-profile/
runtime/deepseek_api_key.txt
bepinex/**/bin/
bepinex/**/obj/
*.dll
*.pdb
*.log
.env
*.key
*.secret
```

分享项目给别人前，检查不要包含 API Key。

---

## 16. 每次新对话上传建议

最小组合：

```text
PROJECT_CONTEXT_FOR_CHATGPT.md
project_tree_clean.txt
当前问题相关代码文件
当前问题相关数据片段
错误截图或终端 traceback
```

按问题类型：

### 事件缺失 / event_overrides

```text
src/data_loader.py
src/web_app.py
src/recommender.py
data/event_overrides.json
data/events.json 相关片段
runtime/missing_events.json 相关片段
```

### AI 分析问题

```text
src/web_app.py
src/ai_advisor.py
src/recommender.py
```

### 推荐卡池算错

```text
src/recommender.py
src/data_loader.py
data/events.json 相关片段
data/event_overrides.json
data/cards_generated.json 相关卡牌片段
data/card_ratings.json 相关片段
data/rarity_rules.json
```

### BepInEx 实时状态问题

```text
bepinex/BazaarStateExporter/Plugin.cs
bepinex/BazaarStateExporter/JsonStateWriter.cs
bepinex/BazaarStateExporter/NetMessagePatches.cs
bepinex/BazaarStateExporter/StateSnapshot.cs
runtime/game_state.json
```

### UI 炸了

```text
src/web_app.py
runtime/game_state.json
终端 traceback
错误截图
```

---

## 17. 给 ChatGPT / Codex 的通用要求

```text
请先判断问题出在哪个文件，不要大改架构。
优先给最小修改方案。
如果要改函数，请给完整可替换函数。
不要重构整个项目。
不要改无关文件。
如果涉及 JSON 数据，说明应该写入 events.json 还是 event_overrides.json。
```

---

## 18. 当前开发优先级

```text
1. 保证 UI 不炸
2. 清理 web_app.py 重复函数
3. 保证 PVP / 怪物 / combat 不进入推荐和 missing event
4. 保证缺失事件不阻断 AI 分析
5. 保证 event_overrides.json 可以稳定修正事件
6. 继续补事件数据
7. 优化 AI 输出
8. 扩展更多英雄和阵容
```
