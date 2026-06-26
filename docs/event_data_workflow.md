# Event Data Workflow

事件数据建议用 CSV 维护，再生成 `data/events.json` 给推荐系统读取。

## 为什么不直接手写 JSON

`events.json` 是程序友好的结构，但不适合人工长期维护：

- 商店、资源事件、道具事件字段不同
- `shop_pool`、`card_reward` 这类嵌套结构容易漏字段
- tag、rarity、rarity_rule 写错后很难肉眼发现
- 从网页、表格、游戏记录整理数据时，CSV 更容易批量复制和修正

推荐流程：

```text
raw_data/events.csv  ->  scripts/import_events.py  ->  data/events.json
```

## CSV 字段

基础字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `name` | 商店或事件名称 | `Nautica` |
| `category` / `event_type` | 类型 | `shop`, `skill_shop`, `item_event`, `resource_event` |
| `notes` | 备注 | `Sells Aquatic items.` |

卡池字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `reward_tags` | 可出现卡牌标签，逗号分隔 | `aquatic,ammo` |
| `target_tags` | item event 影响的标签，逗号分隔 | `poison` |
| `match_mode` | 标签匹配方式 | `any` 或 `all` |
| `excluded_tags` | 排除标签 | `legendary` |
| `rarity_min` | 固定最低稀有度 | `silver` |
| `rarity_max` | 固定最高稀有度 | `gold` |
| `rarity_rule` | 按天数变化的稀有度规则 | `normal_shop_by_day` |

资源字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `gold` | 金币奖励 | `3` |
| `exp` | 经验奖励 | `1` |
| `health` | 生命奖励 | `5` |

其他字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `shop_type` | 商店分类，用于展示或调试 | `aquatic` |
| `hero_filter` | 限定英雄，不要写成 tag | `Vanessa` |
| `effect` | item event 效果 | `improve_items` |

## 常见填写规则

普通标签商店：

```csv
name,category,shop_type,reward_tags,match_mode,rarity_rule,excluded_tags,notes
Nautica,shop,aquatic,aquatic,any,normal_shop_by_day,legendary,Sells Aquatic items.
```

固定稀有度商店：

```csv
name,category,shop_type,reward_tags,match_mode,rarity_min,rarity_max,excluded_tags,notes
Goldie,shop,gold,,any,gold,gold,legendary,Sells Gold-tier items.
```

强化某类物品的事件：

```csv
name,category,target_tags,match_mode,effect,health,notes
Mad Maddie,item_event,ammo,any,improve_items,5,Improves Ammo items.
```

资源事件：

```csv
name,category,gold,exp,health,notes
Treasure Turtle,resource_event,6,0,0,Gives gold.
```

英雄商店：

```csv
name,category,shop_type,hero_filter,reward_tags,match_mode,rarity_rule,excluded_tags,notes
Vanessa,shop,hero,Vanessa,,any,normal_shop_by_day,legendary,Sells Vanessa items.
```

注意：`Vanessa` 是英雄字段，不是卡牌 tag。不要把它填到 `reward_tags`。

## 导入和校验

只校验，不覆盖 JSON：

```bash
python scripts/import_events.py --check-only
```

生成 `data/events.json`：

```bash
python scripts/import_events.py
```

脚本会检查：

- 重复事件名
- 未知 tag
- 未知稀有度
- 未知 `match_mode`
- 不存在的 `rarity_rule`

## 推荐的数据维护方式

1. 从 Wiki、截图、游戏内记录整理到 `raw_data/events.csv`
2. 每次改完先运行 `python scripts/import_events.py --check-only`
3. 没有警告后运行 `python scripts/import_events.py`
4. 再运行推荐 demo 看结果是否符合直觉
