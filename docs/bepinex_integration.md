# BepInEx 接入计划

## 目标

BepInEx 插件只负责导出结构化游戏状态，不做推荐、不调用 AI、不维护 build 知识。

现有 Python/UI 侧已经轮询：

```text
D:\bazzarhelp\runtime\game_state.json
```

插件写入这个文件后，Web UI 会自动刷新分析结果。

## 最小可用字段

```json
{
  "source": "bepinex",
  "hero": "Vanessa",
  "day": 6,
  "event_options": ["Colt", "Kina", "Gaseo"]
}
```

增强字段：

```json
{
  "owned_cards": [
    {"name": "Ballista", "rarity": "gold", "enchantments": ["Fiery"]}
  ],
  "visible_cards": [
    {"name": "Ambergris", "rarity": "silver"}
  ],
  "gold": 12,
  "health": 43
}
```

不要从插件写 `build`。UI 会根据 hero/day 自动选择，也允许用户在 UI 里手动切换。

## 工作拆分

1. 安装 BepInEx 到 The Bazaar 游戏目录，确认插件日志能加载。
2. 编译 `bepinex/BazaarStateExporter`，把 DLL 放入 `BepInEx/plugins/BazaarStateExporter/`。
3. 在插件配置里把 `OutputPath` 指向 `D:\bazzarhelp\runtime\game_state.json`。
4. 先开启 `WritePlaceholderWhenEmpty = true` 做烟测，确认 UI 能读到插件写出的 JSON。
5. 实现 `StateProbe.TryReadCurrentState()`：
   - 当前英雄
   - 当前天数
   - 当前事件/商店选项名称
   - 已拥有物品名称、稀有度、附魔
   - 当前可见商店物品
   - 金币和生命
6. 关闭占位输出，用真实游戏状态验证每个页面：
   - 事件选择页
   - 商店页
   - 物品升级/附魔事件
   - 战斗或非商店事件

## 探针实现建议

优先用游戏内对象和数据 ID，不要 OCR 屏幕文字。

推荐顺序：

1. 先在 BepInEx 日志里打印已加载 assemblies 和疑似 manager 类型。
2. 找到保存 run/session/player/shop/encounter 状态的对象。
3. 先读名字字段，跑通 JSON。
4. 再补内部 ID、稀有度、附魔和资源。
5. 如果字段名混淆或对象难找，再加 Harmony patch 捕获状态更新方法的参数。

## 文件写入要求

插件必须原子写入：

```text
game_state.json.tmp -> game_state.json
```

这样 UI 轮询时不会读到半截 JSON。
