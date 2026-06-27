# PROJECT_CONTEXT_FOR_CHATGPT

## 1. 项目一句话

这是一个本地运行的《The Bazaar》AI 决策助手项目：通过 BepInEx 插件导出结构化游戏状态，Python Web UI 读取状态并展示事件推荐，规则系统先计算事件/商店收益，DeepSeek 只负责把结构化结果解释成中文短建议。

核心原则：

* 主输入是结构化状态，不依赖 OCR。
* 规则系统负责事实计算，AI 负责解释，AI 不能编造事件、卡牌、概率或规则。
* `data/events.json` 是基础事件库；人工修正和新增优先写 `data/event_overrides.json`。
* 尽量做最小改动，不要随便重构架构。
* 推荐等级、父子事件收益、技能收益兜底都应优先在规则层和展示层明确计算，AI 只负责解释。

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

* 推荐系统需要 card id、internal name、tags、hero、rarity、event pool rule、owned cards、day、build roles。
* 这些信息大多不稳定显示在游戏 UI 文本里。
* OCR 容易误识别，污染后续推荐结果。
* 推荐核心 `recommender.py` 不应该知道 OCR 的存在。

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

* `dict`：递归深度合并。
* `list`：整体替换，不是追加。
* 普通字段：override 覆盖 base。
* 新增事件时必须写 `name`、`source_id`、`source_ids`、`event_category` 等基础字段。
* 已有事件可以只写需要修改的字段。

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

注意：

* 不确定收益时，不要乱写 `card_reward` 或 `shop_pool`。
* 错误卡池比缺失数据更危险。
* 真正稳定的事件规则应写入 `data/event_overrides.json`，不要只依赖 `runtime/observed_event_graph.json`。

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

* 没有 `count`：默认 1。
* `count: 2`：按获得 2 个物品计算。
* 非法值/空值：回退 1。
* 商店/技能商店仍默认 `SHOP_CARD_COUNT = 6`。

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

### skill_shops / skill_events

技能收益事件。

常见结构可以是：

```json
{
  "event_category": "skill_shops",
  "event_type": "skill_shop",
  "notes": "Choose 1 of 2 Skills."
}
```

或：

```json
{
  "event_type": "skill_reward",
  "qualitative_rewards": ["skill"],
  "notes": "Choose 1 of 2 Skills."
}
```

当前规则：

* 明确有技能收益的事件最低按 `Medium Value / 可以考虑` 处理。
* 技能收益不等同于普通卡池收益。
* 技能收益事件即使没有核心卡命中，也不应显示为 Low Value。

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

* 不确定就先放 unknown。
* 不要乱写 `card_reward`。
* 不要乱写 `shop_pool`。
* 错误卡池比缺失数据更危险。

---

## 6. 推荐器当前逻辑

`src/recommender.py` 负责：

* 根据事件规则推断候选卡池。
* 根据标签、品质、英雄池、尺寸、固定卡名筛选卡牌。
* 判断卡牌在当前 Build 中是 core / transition / optional / unrelated。
* 计算相关卡数量、核心卡数量、S/A 卡数量。
* 计算至少命中相关卡/核心卡概率。
* 分析已有卡牌是否可升级。
* 分析资源收益、已有物品命中、附魔/升级收益。
* 分析 `followup_options`。
* 判断技能收益事件。
* 输出推荐等级和理由。

推荐等级：

```text
High Value   优先选择
Medium Value 可以考虑
Low Value    优先级低
```

### 父子事件 / followup_options 规则

父事件和子事件收益不能简单相加。

原因：

```text
选择父事件后，只能在后续子事件中选择一个。
```

正确处理方式：

```text
父事件直接收益 = 父事件自己的资源 / 卡池 / 统计
最佳后续收益 = 单独记录在 best_followup / followup_value_summary
父事件推荐等级 = 可以参考最佳后续价值提升
父事件 resource_rewards / pool_stats 不应被子事件覆盖
```

错误处理方式：

```text
父事件资源收益 = 父事件收益 + 子事件收益
父事件统计 = 子事件统计
多个子事件收益相加
```

当前约定：

* `analyze_event()` 会递归分析 `followup_options`。
* `apply_followup_value()` 用最佳子事件影响父事件推荐等级。
* `summarize_best_followup_value()` 只保存最佳后续摘要。
* 父事件主字段 `resource_rewards`、`pool_stats` 保留父事件直接收益。
* 子事件收益通过 `followup_options`、`best_followup`、`followup_value_summary` 单独展示和传给 AI。
* 如果父事件直接无收益，但存在后续收益，不应再显示“暂未识别到明确的卡牌或资源收益”。

### 技能收益事件规则

所有技能收益事件最低按 `Medium Value / 可以考虑` 处理。

识别范围包括：

```text
event_category == skill_shops
event_type == skill_shop / skill_event / skill_reward
effect == gain_skill / choose_skill / skill_reward
qualitative_rewards 中包含 skill / 技能
notes / description 中包含 skill、skills、Choose 1 of 2 Skills、Choose 1 of 3 Skills、gain a skill、技能
```

推荐器中应使用：

```python
event_has_skill_reward(event_data)
```

注意：

* 技能收益不是普通卡池收益。
* 即使当前 Build 下没有核心卡命中，只要明确有技能收益，也不应显示为 Low Value。
* 技能收益事件可以是普通事件，也可以是父事件的子选项。
* `has_skill_reward` 应在 `decide_recommendation()` 中使用，不要放在 `analyze_event()` 的卡牌循环里。

推荐结构：

```python
def decide_recommendation(...):
    reasons: list[str] = []
    ...
    has_skill_reward = event_has_skill_reward(event_data)

    if has_skill_reward:
        reasons.append("包含技能收益，最低按可以考虑处理。")

    ...

    if (
        not analyzed_cards
        and not has_resource_reward
        and not has_skill_reward
        and not owned_target_hits
        and event_data.get("event_category") != "enchant_events"
    ):
        reasons.append("暂未识别到明确的卡牌或资源收益。")

    ...

    if has_skill_reward:
        return "Medium Value", reasons
```

### get_event_draw_count() 当前规则

```text
shops / skill_shops → SHOP_CARD_COUNT = 6
item_rewards → 默认 1
card_reward.count 存在且合法 → 使用 count
count 缺失 / None / 非法 → 回退 1
```

注意：

如果 `analyze_event()` 中仍有：

```python
card_reward = event_data.get("card_reward", {})
reward_count = int(card_reward.get("count", 1)) if isinstance(card_reward, dict) else 1
```

而后面已经使用：

```python
draw_count = get_event_draw_count(event_data)
```

则前两行是废变量，可以删除。

---

## 7. Web UI 与运行时状态

`src/web_app.py` 负责：

* 启动本地 Web UI。
* 读取 `runtime/game_state.json`。
* 没有实时状态时读取 `examples/game_state.example.json`。
* 归一化事件选项和卡牌条目。
* 自动匹配 Build。
* 调用 `advisor.py` 得到规则推荐。
* 调用 `ai_advisor.py` 得到 AI 分析。
* 记录缺失事件到 `runtime/missing_events.json`。
* 维护父子事件观察图 `runtime/observed_event_graph.json`。
* 整理推荐结果给前端展示。
* 把展示层修正后的推荐等级和理由传给 AI。

常用 API：

```text
GET  /
GET  /api/state
GET  /api/options
GET  /api/analysis
POST /api/state
```

### 展示层推荐修正规则

`web_app.py` 的 `summarize_recommendation()` 会对推荐器结果做展示层整理：

* 未知事件：显示为事件数据缺失。
* 已知但无收益规则：显示为已识别，暂无收益规则。
* 父事件：展示运行时观察到的子选项。
* 父事件有技能子选项：最低显示为可以考虑。
* 普通技能收益事件：最低显示为可以考虑。
* 父事件不应强制改成固定 Medium Value，而应尽量保留推荐器计算结果；只有技能收益兜底时才提升到 Medium Value。
* 父事件有后续时，要过滤掉“暂未识别到明确的卡牌或资源收益”等旧提示。
* UI 卡片显示的推荐等级如果被展示层修正，顶部 AI 分析也必须吃到同样的修正。

### 父子事件观察图

`runtime/observed_event_graph.json` 用来记录运行时观察到的父子事件关系。

当前约定：

```text
一个父事件 + 至少一个 step/combat/pvp 子选项 → 写入 observed_event_graph
children 是并集，不因为本次没出现某个子选项就删除
子选项会尝试从官方 cards.json 中补充 name、description、resource_rewards
```

注意：

* observed_event_graph 是运行时观察图，不是最终事件库。
* 它可以用于 UI 展示和临时收益估算。
* 真正稳定的事件规则仍应写入 data/event_overrides.json。
* 如果父事件的子选项 description 中包含 Skills，也应触发技能收益兜底。

### 技能收益展示层兜底

`web_app.py` 应有以下判断函数：

```text
event_has_skill_reward(event_data)
child_option_has_skill_reward(child)
child_options_have_skill_reward(child_options)
```

用途：

* 普通技能事件最低显示为可以考虑。
* 父事件的子选项里有技能收益时，父事件最低显示为可以考虑。
* AI 输入要吃到展示层修正后的推荐等级。

### AI 输入规则

当前 AI 分析入口：

```text
include_ai and response["recommendations"]
```

AI 不应该因为 `warnings` 存在就直接停止。缺失数据应该降低置信度，而不是完全阻断 AI。

重要：

`ai_advisor.py` 不能只吃 `result.recommendations` 的原始结果。
如果 `web_app.py` 展示层已经把技能事件从 Low Value 提升到 Medium Value，AI 输入也必须同步这个修正。

推荐写法：

```text
先生成 response["recommendations"]
再把展示层修正后的 recommendation / reasons 合并回 ai_results
最后调用 compact_recommendations(..., results=ai_results)
```

这样顶部 AI 分析才会和卡片推荐等级一致。

---

## 8. PVP / 怪物 / 战斗过滤

目标：

* PVP 不参与推荐。
* 怪物战斗不参与推荐。
* CombatEncounter 不应该写入 `missing_events.json`。
* 普通 EventEncounter 仍然保留。

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

* graph 节点不是 dict。
* `children` 是 null。
* child 不是 dict。
* parent_record 是 None。
* 旧文件里存在坏结构。

当前 `web_app.py` 已有防御式处理：

* `load_observed_event_graph()` 会清洗节点。
* `_coerce_observed_graph_node()` 会修正 `parent_source_ids`、`children`、`observed_count`。
* `write_observed_event_graph()` 写入前会清洗。
* `analyze_payload()` 调用 `auto_observe_event_graph()` 时有 try/except，观察图失败不应影响主分析。

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
6. 确认收益后再补 shop_pool / card_reward / resource_rewards / qualitative_rewards
7. 保存后重启 UI
```

unknown_event 示例：

```json
{
  "事件名": {
    "_override_reason": "人工新增：events.json 中缺失，收益规则待测试。",
    "name": "事件名",
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

技能事件但没有完整卡池时，可以先写：

```json
{
  "事件名": {
    "_override_reason": "人工修正：该事件提供技能奖励。",
    "event_type": "skill_reward",
    "qualitative_rewards": ["skill"],
    "notes": "Choose 1 of 2 Skills."
  }
}
```

---

## 11. AI 分析层

`src/ai_advisor.py` 负责：

* 把推荐器结果压缩成中文结构化 payload。
* 调 DeepSeek。
* 清理 Markdown 符号。
* 输出短建议。

当前要求：

* 必须中文。
* 不输出 Markdown。
* 总字数控制短。
* 只能基于输入数据判断。
* 不得编造卡牌、事件、概率或规则。
* 必须严格遵守推荐等级排序。
* 不能在存在“可以考虑”或“优先选择”的事件时，推荐“优先级低”的事件。
* 不能把多个子事件收益相加。
* 不能把子事件收益当作父事件直接收益。

推荐排序规则：

```text
优先选择 > 可以考虑 > 优先级低
```

AI 输出时：

```text
如果存在“优先选择”，推荐必须从“优先选择”里选。
如果不存在“优先选择”，但存在“可以考虑”，推荐必须从“可以考虑”里选。
只有所有选项都是“优先级低”时，才允许推荐“优先级低”。
禁止因为文字描述看起来不错，就推荐等级更低的事件。
```

父子事件规则：

```text
如果事件有后续选项，表示选择父事件后只能再从后续子事件里选一个。
不要把多个子事件收益相加。
不要把子事件收益当作父事件直接收益。
```

AI payload 应明确区分：

```text
父事件直接资源收益
父事件直接统计
最佳后续
最佳后续收益
后续选项
```

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

后续应做一次最小清理：

```text
只保留最后版本 / 更安全版本
不改变逻辑
不重构架构
不改无关文件
```

### recommender.py 技能收益逻辑位置

`event_has_skill_reward(event_data)` 应该是独立辅助函数。

不要在 `analyze_event()` 的卡牌循环里写：

```python
has_skill_reward = event_has_skill_reward(event_data)

if has_skill_reward:
    reasons.append(...)
```

原因：

```text
analyze_event() 此时还没有 reasons 变量，容易出现 NameError。
```

正确位置：

```text
在 decide_recommendation() 内部创建 reasons 后使用。
```

推荐结构：

```python
has_skill_reward = event_has_skill_reward(event_data)

if has_skill_reward:
    reasons.append("包含技能收益，最低按可以考虑处理。")

if has_skill_reward:
    return "Medium Value", reasons
```

并且“暂无收益”判断要排除技能收益：

```python
not has_skill_reward
```

### web_app.py 技能收益展示层兜底

`web_app.py` 需要有展示层技能收益判断：

```text
event_has_skill_reward(event_data)
child_option_has_skill_reward(child)
child_options_have_skill_reward(child_options)
```

用途：

* 普通技能事件最低显示为可以考虑。
* 父事件的子选项里有技能收益时，父事件最低显示为可以考虑。
* AI 输入要吃到展示层修正后的推荐等级。

### 父子事件收益不要合并

不要把最佳子事件的：

```text
resource_rewards
pool_stats
```

覆盖到父事件主字段。

父事件主字段只表示直接收益。
子事件收益必须通过：

```text
followup_options
best_followup
followup_value_summary
child_options
best_followup_summary
```

单独展示。

### AI 推荐和 UI 卡片要一致

如果 UI 卡片已经把某个事件提升到：

```text
Medium Value / 可以考虑
```

AI 顶部总结也必须基于同样结果判断。

常见问题：

```text
卡片显示“可以考虑”，但 AI 仍推荐“优先级低”的事件。
```

优先检查：

```text
web_app.py -> analyze_payload()
是否把 response["recommendations"] 的 recommendation / reasons 合并回 ai_results
```

### 每次改完优先检查

```powershell
python -m py_compile src\recommender.py src\web_app.py src\ai_advisor.py
python -m pytest
```

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

指定端口快速手测：

```powershell
python src\web_app.py --port 8765
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
python -m py_compile src\recommender.py src\web_app.py src\ai_advisor.py
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

浏览器强刷：

```text
Ctrl + F5
```

如果 UI 使用旧逻辑，优先确认：

```text
1. Python 服务是否重启
2. 浏览器是否强刷
3. VS Code 是否保存到了正确文件
4. 是否被 Compare / Overwrite 覆盖回旧版本
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

推荐提交当前稳定版本：

```powershell
git status
git add src\recommender.py src\web_app.py src\ai_advisor.py PROJECT_CONTEXT_FOR_CHATGPT.md
git commit -m "fix followup and skill event recommendations"
```

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

尤其要检查：

```text
web_app.py -> analyze_payload()
ai_advisor.py -> compact_recommendations()
ai_advisor.py -> build_ai_messages()
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

### 父子事件 / followup_options 问题

```text
src/recommender.py
src/web_app.py
src/ai_advisor.py
runtime/observed_event_graph.json 相关片段
data/events.json 相关事件片段
data/event_overrides.json 相关片段
```

### 技能收益识别问题

```text
src/recommender.py
src/web_app.py
src/ai_advisor.py
data/events.json 相关事件片段
data/event_overrides.json 相关片段
runtime/observed_event_graph.json 相关片段
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
如果涉及父子事件，注意子事件收益不能合并进父事件直接收益。
如果涉及技能事件，所有技能收益事件最低按“可以考虑”处理。
如果 UI 卡片和 AI 总结不一致，优先检查 web_app.py 是否把展示层修正后的推荐传给 ai_advisor.py。
```

---

## 18. 当前开发优先级

```text
1. 保证 UI 不炸
2. 清理 web_app.py 重复函数
3. 保证 PVP / 怪物 / combat 不进入推荐和 missing event
4. 保证缺失事件不阻断 AI 分析
5. 保证 event_overrides.json 可以稳定修正事件
6. 保证父子事件收益不合并，后续收益单独展示
7. 保证技能收益事件最低显示为可以考虑
8. 优化 AI 输出，并保证 AI 推荐等级与 UI 卡片一致
9. 继续补事件数据
10. 扩展更多英雄和阵容
```

---

## 19. 当前稳定规则总结

### 父子事件

```text
父事件直接收益只看父事件自己。
子事件收益只能作为后续收益单独展示。
父事件可以因为最佳后续变得更值得选。
不能把多个子事件收益相加。
不能把子事件收益覆盖到父事件 resource_rewards / pool_stats。
```

### 技能事件

```text
只要明确有技能收益，最低 Medium Value / 可以考虑。
普通事件、技能商店、父事件子选项都适用。
AI 和 UI 都要同步这个推荐等级。
```

### AI 总结

```text
AI 必须遵守推荐等级排序。
存在“可以考虑”时，不能推荐“优先级低”。
存在“优先选择”时，优先从“优先选择”中推荐。
AI 不能自己重新发明规则，只能解释结构化结果。
```

### 数据修正

```text
稳定修正写 data/event_overrides.json。
运行时观察写 runtime/observed_event_graph.json。
不确定收益先 unknown，不要乱写卡池。
```
