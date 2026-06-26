# Architecture Decision: Realtime Input Strategy

## Decision

The Bazaar AI 助手的核心输入必须是结构化游戏状态。

优先级：

1. BepInEx / Unity 插件导出的结构化状态
2. 官方 cache JSON 或其他官方数据文件
3. 手动结构化输入
4. OCR 文本，仅作为辅助输入

OCR 不作为商店卡牌识别或实时推荐的核心数据管道。

## Context

项目目标是根据《The Bazaar》的商店/事件状态，分析卡牌收益、流派匹配和决策建议。

推荐系统需要依赖以下关键特征：

- card id / internal name
- card tags
- hero
- rarity range
- shop/event pool rules
- owned cards
- current day
- build roles

这些特征大多不是稳定显示在 UI 上的文本。

## Why OCR Is Not Core

OCR 不适合作为核心方案：

- 游戏 UI 以图标、颜色、稀有度和卡牌视觉信息为主
- card tags、internal card id、shop 生成逻辑和概率不在 UI 文本中完整展示
- tooltip 需要悬停，且可能被遮挡或动态消失
- OCR 识别错误会导致卡牌匹配错误，污染后续推荐逻辑

因此 OCR 只能用于：

- 原型验证
- 截图临时补充
- 无插件环境下的辅助输入

OCR 不能用于：

- 核心商店卡牌识别
- 实时数据主来源
- 推荐系统正确性的前提条件

## Target Architecture

```text
Game / Official Data
  ├─ BepInEx plugin state JSON
  ├─ official cache JSON
  └─ optional OCR/manual input

Python Analysis
  ├─ GameState model
  ├─ data loader
  ├─ recommender
  └─ advisor orchestration

Presentation
  ├─ CLI
  ├─ Web UI
  └─ overlay
```

## Plugin State Contract

未来 BepInEx 插件建议只做一件事：导出结构化状态。

示例：

```json
{
  "source": "plugin",
  "hero": "Vanessa",
  "build": "VanessaAquaticAmmo",
  "day": 5,
  "event_options": ["Nautica", "Colt", "Goldie"],
  "visible_cards": [
    {
      "id": "42f78ed2-0141-47f3-9bcd-71b433b1273b",
      "name": "Ambergris",
      "rarity": "silver"
    }
  ],
  "owned_cards": [
    {
      "id": "8f18974c-eef9-4e82-a2d2-7f4e7c67daf8",
      "name": "Burnacuda",
      "rarity": "bronze"
    }
  ],
  "gold": 12,
  "health": 43
}
```

Python 侧只依赖这个结构，不依赖游戏 UI 截图。

## Development Rule

后续开发必须遵守：

- 不依赖 OCR 输出作为结构化卡牌数据来源
- 主逻辑只接受结构化输入
- OCR 只能作为独立实验模块接入，不能进入推荐主路径
- 推荐核心 `recommender.py` 不应该知道 OCR 的存在
