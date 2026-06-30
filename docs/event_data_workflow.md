# 事件数据工作流

事件事实数据来自游戏客户端收到并加载的官方数据，不再维护人工 CSV 或第三方静态 JSON。

## 主流程

```text
游戏客户端缓存 / BepInEx 运行时扫描
  -> cards_generated.json
  -> skills_generated.json
  -> encounters_generated.json
  -> build_events_from_encounters.py
  -> events.json
```

生成器无法可靠表达的特殊事件写入 `data/event_overrides.json`。加载时 overrides 会覆盖生成结果，因此不要长期直接编辑 `data/events.json`。

## 更新数据

从官方客户端缓存导入：

```powershell
python scripts\import_game_cache.py
```

从 BepInEx 运行时扫描结果导入：

```powershell
python scripts\import_live_cards.py
```

仅重新生成事件：

```powershell
python scripts\build_events_from_encounters.py
```

## 审计

检查单个事件卡池：

```powershell
python scripts\audit_event_pool.py --event Ande --hero Karnok --day 3
```

查找已识别但暂无收益规则的事件：

```powershell
python scripts\audit_event_rules.py
```

## 人工知识

项目只保留两类人工推荐知识：

- `data/community_builds.json`：社区阵容和卡牌在阵容内的定位。
- `data/card_ratings.json`：单一的全局卡牌评级文件。

卡牌、技能、Encounter 和事件基础事实不得写入这两个文件。

## 修改后的检查顺序

```text
修改生成器或 event_overrides
  -> 重新生成 events.json
  -> 运行事件审计
  -> 运行 pytest
  -> 游戏内验证
```
