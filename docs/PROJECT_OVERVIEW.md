# BazaarHelp 项目说明

## 1. 项目目标

BazaarHelp 是一个面向《The Bazaar》的本地决策辅助工具。

项目目标是根据玩家当前局内状态、英雄、天数、已有卡牌、当前事件选项和阵容 Build 数据，分析每个事件/商店的潜在收益，并用可解释的方式辅助玩家做选择。

当前项目重点不是“替玩家强行决定唯一答案”，而是提供：

* 当前事件可能产出的卡池
* 核心卡、过渡卡、可选卡命中情况
* 相关卡命中概率
* 核心卡命中概率
* 升级收益
* 资源收益
* 低价值选择的原因
* 可选 AI 中文分析解释

---

## 2. 当前项目状态

项目已经从早期命令行脚本发展为：

```text
BepInEx 实时状态导出
→ Python 数据读取
→ Build 自动/手动匹配
→ 事件收益分析
→ Web UI 展示
→ 可选 DeepSeek AI 分析
```

当前主线是：

1. 通过 BepInEx 插件或手动 JSON 获取局内状态。
2. 读取 `data/` 下的卡牌、事件、阵容、评级和稀有度规则。
3. 根据当前英雄、天数、Build 和已有卡牌分析事件收益。
4. 在本地 Web UI 中展示推荐结果。
5. 用户可点击 AI 分析，生成中文解释。

---

## 3. 项目目录结构

```text
BAZZARHELP/
├─ src/                  # Python 主程序与核心逻辑
├─ scripts/              # 数据转换脚本
├─ data/                 # 正式数据文件
├─ bepinex/              # BepInEx 插件项目
├─ docs/                 # 项目说明文档
├─ examples/             # 示例输入文件
├─ tests/                # 测试文件
├─ runtime/              # 运行时状态文件，不进入 Git
├─ outputs/              # 运行输出文件，不进入 Git
├─ tmp/                  # 临时文件，不进入 Git
├─ .venv/                # Python 虚拟环境，不进入 Git
├─ README.md
├─ requirements.txt
├─ start_ui.bat
└─ start_ui.ps1
```

---

## 4. 核心模块说明

### `src/data_loader.py`

负责加载项目数据，并把卡牌官方数据和评分数据合并。

主要加载：

* `data/cards_generated.json`
* `data/card_ratings.json`
* `data/events.json`
* `data/community_builds.json`
* `data/rarity_rules.json`
* `data/translations_zh_cn.json`

输出统一的 `data` 字典，供后续推荐逻辑使用。

---

### `src/game_state.py`

负责定义当前游戏状态 `GameState`。

当前状态包括：

* 英雄
* 当前 Build
* 当前天数
* 当前事件选项
* 已拥有卡牌
* 已拥有卡牌附魔
* 可见卡牌
* 金币
* 血量
* 状态来源

它也负责校验：

* 英雄是否存在
* Build 是否存在
* Build 是否适用于当前英雄
* 天数是否合法
* 事件是否已收录
* 事件是否适用于当前英雄
* 已拥有卡牌是否存在

---

### `src/build_strategy.py`

负责判断当前游戏阶段和 Build 适用性。

当前阶段划分：

```text
Day 1-4   → early / 前期
Day 5-8   → mid / 中期
Day 9+    → late / 后期
```

Build 可以通过两种方式判断适用时机：

1. `applicable_stages`
2. `day_range`

---

### `src/recommender.py`

项目核心推荐逻辑。

负责：

* 根据事件规则推断候选卡池
* 根据标签筛选卡牌
* 根据稀有度规则筛选卡牌
* 根据英雄范围筛选卡牌
* 判断卡牌在当前 Build 中的定位
* 计算核心卡、过渡卡、可选卡数量
* 计算相关卡命中概率
* 计算核心卡命中概率
* 判断已有卡牌是否可升级
* 分析资源收益
* 分析事件后续选项
* 输出推荐等级和推荐理由

当前推荐等级：

```text
High Value   → 优先选择
Medium Value → 可以考虑
Low Value    → 优先级低
```

当前数据优先级约定：

```text
community_builds.json = 社区阵容定位主数据
card_ratings.json = 卡牌强度评级和旧定位补充
events.json       = 事件/商店卡池规则
cards_generated.json = 官方卡牌基础数据
```

社区阵容内定位优先于 `card_ratings.json` 的可选定位补充。

---

### `src/advisor.py`

负责把当前状态转换为多事件推荐结果。

流程：

```text
GameState
→ 校验状态
→ 遍历当前事件选项
→ 调用 recommender.analyze_event()
→ 按推荐等级和期望收益排序
→ 返回 AdvisorResult
```

如果遇到未知事件，会生成缺失事件提示，而不是直接让程序崩溃。

---

### `src/web_app.py`

本地 Web UI 和 API 服务。

主要职责：

* 读取 `runtime/game_state.json`
* 如果没有实时状态，则读取示例状态文件
* 归一化事件 ID、事件名、卡牌 ID、卡牌名
* 根据已有卡牌自动匹配合适 Build
* 调用 `advisor.py` 生成分析结果
* 提供本地 HTTP API
* 返回 Web 页面展示分析结果
* 记录未收录事件到 `runtime/missing_events.json`

常用接口：

```text
GET  /
GET  /api/state
GET  /api/options
GET  /api/analysis
POST /api/state
```

---

### `src/ai_advisor.py`

负责把推荐结果压缩成结构化摘要，并调用 DeepSeek API 生成中文解释。

AI 分析原则：

* 只能基于结构化输入分析
* 不允许编造卡牌、事件或规则
* 需要绑定当前 Build、当前阶段、关键卡、资源收益和实战 Tips
* 信息不足时必须说明无法判断

API Key 支持两种方式：

```text
环境变量 DEEPSEEK_API_KEY
runtime/deepseek_api_key.txt
```

注意：`runtime/deepseek_api_key.txt` 不应进入 Git。

---

### `src/main.py`

命令行入口。

适合用于：

* 快速测试推荐逻辑
* 对比多个事件
* 测试指定英雄、Build、天数和事件
* 预览 AI Prompt Payload
* 读取状态 JSON 做离线分析

示例：

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie
```

---

### `scripts/import_community_builds.py`

社区阵容模板转换脚本。

作用：

```text
社区阵容 Excel 模板
→ 校验字段
→ 转换为 JSON
→ 写入 data/community_builds.json
```

主要读取工作表：

```text
阵容主表
阵容卡牌
选项
卡牌参考
阵容来源
```

常用命令：

```powershell
python scripts\import_community_builds.py outputs\community_build_template\社区阵容录入模板.xlsx
```

如果模板中有错误，默认会停止输出。可以用下面参数强制输出：

```powershell
--allow-errors
```

---

## 5. 核心数据文件

### `data/cards_generated.json`

官方卡牌基础数据。

主要字段包括：

* 英雄
* 类型
* 尺寸
* 标签
* 最低稀有度
* 最高稀有度
* 可出现稀有度
* 价格
* 描述
* 内部 ID

---

### `data/card_ratings.json`

卡牌评级与旧 Build 定位数据。

主要字段：

```json
{
  "Card Name": {
    "tier": "A",
    "build_roles": {
      "BuildName": "core"
    }
  }
}
```

注意：

* `tier` 是玩家强度评级，不是游戏内品质。
* `build_roles` 只作为补充。
* 当前项目以 `community_builds.json` 的卡牌定位为主。

---

### `data/events.json`

事件和商店规则数据。

主要包含：

* 商店事件
* 技能事件
* 资源事件
* 物品奖励事件
* 附魔事件
* 事件可用英雄
* 事件来源 ID
* 事件卡池规则
* 稀有度过滤规则
* 标签过滤规则
* 固定奖励卡牌

事件卡池常见字段：

```json
{
  "reward_tags": ["aquatic"],
  "match_mode": "any",
  "rarity_rule": "normal_shop_by_day",
  "excluded_tags": ["legendary"],
  "hero_scope": "current"
}
```

---

### `data/community_builds.json`

阵容 Build 主数据。

这是当前项目中最重要的策略数据。

主要字段：

```json
{
  "BuildName": {
    "hero": "Vanessa",
    "display_name": "Vanessa Aquatic Ammo",
    "applicable_stages": ["mid", "late"],
    "day_range": [5, null],
    "build_summary": "...",
    "match_notes": [],
    "pilot_tips": [],
    "core_cards": [],
    "transition_cards": [],
    "optional_cards": [],
    "wanted_tags": [],
    "event_priorities": [],
    "avoid_events": []
  }
}
```

当前约定：

```text
core_cards       = 核心卡，优先级最高
transition_cards = 过渡卡，有助于转型或中期支撑
optional_cards   = 可选卡，有收益但不是绝对核心
wanted_tags      = 当前 Build 关注的标签
pilot_tips       = 实战经验和选择原则
```

---

### `data/rarity_rules.json`

稀有度规则。

主要用于判断不同天数下普通商店可能出现的卡牌品质范围。

例如：

```text
Day 1-? → bronze/silver
Day 5+  → gold/diamond
```

具体以文件内容为准。

---

### `data/translations_zh_cn.json`

中英文名称映射。

主要用于 UI 展示，把英文卡牌名、事件名转换为中文显示。

---

## 6. 运行方式

### 安装依赖

首次运行或重建环境时：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

### 启动 Web UI

推荐使用：

```powershell
.\start_ui.ps1
```

或直接运行：

```powershell
python src\web_app.py
```

---

### 命令行测试

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie
```

---

### 读取状态 JSON 测试

```powershell
python src\main.py --state-json examples\game_state.example.json
```

---

### AI 分析测试

仅预览 AI 输入，不调用 API：

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie --ai-dry-run
```

调用 AI：

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie --ai
```

---

## 7. Git 忽略规则

以下内容不应进入 Git：

```text
.venv/
runtime/
outputs/
tmp/
__pycache__/
.edge-bazaardb-profile/
bepinex/**/bin/
bepinex/**/obj/
*.dll
*.pdb
*.log
.env
*.key
*.secret
```

原因：

* `.venv/` 是本地 Python 环境，体积大且可重建。
* `runtime/` 是运行时状态，可能包含 API Key 或实时游戏状态。
* `outputs/` 是生成物，不应作为源数据提交。
* `tmp/` 是临时文件。
* `bin/obj/*.dll/*.pdb` 是 C# 编译产物。
* `.edge-bazaardb-profile/` 是旧浏览器自动化缓存，已弃用。

---

## 8. 社区阵容录入与转换流程

当前社区阵容数据推荐流程：

```text
别人填写 Excel 模板
→ scripts/import_community_builds.py 校验并转换
→ 输出 data/community_builds.json
→ Web UI 和推荐逻辑读取新 Build
```

转换命令：

```powershell
python scripts\import_community_builds.py path\to\社区阵容录入模板.xlsx
```

如果只是想单独输出，不合并：

```powershell
python scripts\import_community_builds.py path\to\社区阵容录入模板.xlsx
```

如果模板有错误，脚本会列出问题。应优先修模板，而不是直接强制合并。

---

## 9. 推荐测试流程

每次改完代码后建议按这个顺序测试。

### 1. 检查 Git 状态

```powershell
git status
```

### 2. 检查基础推荐逻辑

```powershell
python src\main.py --hero Vanessa --build VanessaAquaticAmmo --day 5 --events Nautica Colt Goldie
```

### 3. 检查状态文件读取

```powershell
python src\main.py --state-json examples\game_state.example.json
```

### 4. 检查 Web UI

```powershell
python src\web_app.py
```

### 5. 检查 Git 体积

```powershell
git count-objects -vH
```

正常情况下，Git 仓库体积应该是 MB 级，不应该出现 GB 级对象。

---

## 10. 当前项目维护原则

### 数据优先级

```text
community_builds.json 社区阵容定位主数据
card_ratings.json    卡牌强度评级和旧定位补充
events.json          事件/商店规则
cards_generated.json 官方卡牌基础数据
rarity_rules.json    天数与稀有度规则
translations_zh_cn.json 中文显示映射
```

### 代码分层原则

```text
数据层：data_loader.py
状态层：game_state.py
阶段/Build 策略层：build_strategy.py
推荐逻辑层：recommender.py
多事件分析层：advisor.py
展示/API 层：web_app.py
AI 解释层：ai_advisor.py
数据导入层：scripts/import_community_builds.py
```

### 修改原则

* 不要让 UI 直接写复杂推荐逻辑。
* 不要让 AI 决策替代规则系统。
* 不要在 `runtime/` 里放需要长期保存的项目文件。
* 不要把 `.venv/`、缓存、编译产物提交进 Git。
* 新增数据结构前，先确认会影响哪些模块。
* `community_builds.json` 里的 Build 结构要尽量稳定。
* AI 只能解释结构化分析结果，不应编造未计算出的概率、事件或卡牌。

---

## 11. 常见问题

### Q1：为什么项目以前会变成 36GB？

主要原因是旧 Git 历史中可能提交过 `.venv/`、浏览器缓存、运行时文件或其他生成物。即使后来删除，Git 历史仍然会保留这些对象。

当前解决方案：

* 删除旧 `.git`
* 重新初始化干净仓库
* 完善 `.gitignore`
* 避免 `.venv/`、`runtime/`、`outputs/` 进入 Git

---

### Q2：为什么不用 OCR 了？

早期考虑过 OCR 和截图识别，但实际项目已经转向：

```text
BepInEx 插件导出结构化状态
→ Python 直接读取状态
```

这种方式比 OCR 稳定，且不依赖图像识别。

---

### Q3：为什么保留 `import_community_builds.py`？

它不是旧爬虫脚本，而是社区阵容模板转换工具。后续让别人填写阵容时仍然需要它。

---

### Q4：为什么 `outputs/community_build_template/` 不适合长期保存模板？

`outputs/` 通常代表运行生成物，不适合保存需要版本控制的正式模板。

如果某个 Excel 模板需要长期维护，建议移动到：

```text
templates/
```

---

### Q5：AI 分析为什么不能直接决定一切？

因为 AI 可能编造不存在的卡牌、事件或概率。当前项目设计是：

```text
规则系统先计算结构化结果
AI 只负责解释这些结果
```

这样更稳定，也更适合给新手玩家解释选择逻辑。

---

## 12. 当前后续计划

短期优先级：

1. 稳定 `community_builds.json` 数据结构。
2. 完善社区阵容模板。
3. 继续补充高质量 Build 数据。
4. 完善事件缺失记录与补全流程。
5. 优化 Web UI 展示。
6. 增加更多测试样例。

中期方向：

1. 根据已有卡牌自动匹配更合适的 Build。
2. 根据当前阶段推荐转型方向。
3. 支持更多英雄的稳定阵容数据。
4. 给每个事件提供更清晰的“为什么值得进/不值得进”解释。
5. 将工具打包给其他玩家测试使用。

长期方向：

1. 形成社区阵容数据收集流程。
2. 形成稳定的事件/商店收益知识库。
3. 让新手能通过本工具理解主流流派、关键卡、转型节点和事件选择。
