from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import web_app
from ai_advisor import build_ai_messages, compact_recommendations
from data_loader import load_all_data
from guide_retriever import (
    build_guide_query,
    guide_hits_to_prompt_context,
    parse_guide_file,
    retrieve_guides_for_ai,
    search_guides,
)


DATA_DIR = PROJECT_ROOT / "data"


@dataclass
class StubState:
    hero: str = "Jules"
    build: str = "ChefBuild"
    day: int = 6
    event_options: list[str] = field(default_factory=list)
    owned_cards: dict[str, str] = field(default_factory=dict)
    owned_items: list[dict] | None = None
    board_items: list[dict] | None = None
    stash_items: list[dict] | None = None
    skills: list[dict] | None = None
    current_shop: dict | None = None
    effective_shop: dict | None = None
    current_reward_options: list[dict] | None = None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _data() -> dict:
    return {
        "translations": {"by_name": {}},
        "builds": {
            "ChefBuild": {
                "hero": "Jules",
                "display_name": "厨师燃烧",
                "core_cards": ["南瓜"],
            }
        },
    }


def test_markdown_splits_by_second_level_heading_and_keeps_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        guide = root / "厨师" / "厨师课堂 S16 上.md"
        _write(
            guide,
            """# 厨师课堂

## 文档信息
- 赛季：S16
- 版本背景：S16 热修后

## 南瓜路线
南瓜、加热箱和减速火互相补足。

### 风险
缺少启动时不要硬转。

## 调料架路线
调料架配食盐和大蒜。
""",
        )

        sections = parse_guide_file(guide, root)

    assert [section.title for section in sections] == ["南瓜路线", "调料架路线"]
    assert "### 风险" in sections[0].body
    assert sections[0].source_file == "厨师/厨师课堂 S16 上.md"
    assert sections[0].hero == "厨师"
    assert sections[0].season == "S16"
    assert sections[0].version_context == "版本背景：S16 热修后"


def test_current_hero_searches_matching_hero_and_common_guides_only() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        _write(root / "厨师" / "a.md", "## 南瓜厨师\n南瓜攻略。")
        _write(root / "通用" / "b.md", "## 南瓜通用\n通用南瓜说明。")
        _write(root / "海盗" / "c.md", "## 南瓜海盗\n南瓜出现很多次，南瓜南瓜南瓜。")

        state = StubState(current_reward_options=[{"name": "南瓜"}])
        hits = search_guides(
            query=build_guide_query(
                data=_data(),
                state=state,
                build_analysis={},
                recommendations=[],
            ),
            guides_dir=root,
        )

    assert {hit.section.hero for hit in hits} == {"厨师", "通用"}


def test_pumpkin_heating_box_and_slow_fire_prioritize_matching_section() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        _write(
            root / "厨师" / "guide.md",
            """## 南瓜加热箱减速火
南瓜、加热箱、减速火是一组明确联动。

## 调料架
调料架是另一条路线。
""",
        )
        state = StubState(
            owned_cards={"加热箱": "gold"},
            skills=[{"name": "减速火"}],
            current_reward_options=[{"name": "南瓜"}],
        )
        context = retrieve_guides_for_ai(
            data=_data(),
            state=state,
            build_analysis={},
            recommendations=[],
            guides_dir=root,
        )

    assert context
    assert context[0]["章节标题"] == "南瓜加热箱减速火"


def test_spice_rack_salt_and_garlic_prioritize_spice_rack_section() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        _write(
            root / "厨师" / "guide.md",
            """## 南瓜路线
南瓜和加热箱。

## 调料架节奏
调料架需要食盐和大蒜支撑，适合补联动。
""",
        )
        state = StubState(
            owned_cards={"食盐": "silver", "大蒜": "silver"},
            current_shop={"visible_items": [{"name": "调料架"}]},
        )
        context = retrieve_guides_for_ai(
            data=_data(),
            state=state,
            build_analysis={},
            recommendations=[],
            guides_dir=root,
        )

    assert context
    assert context[0]["章节标题"] == "调料架节奏"


def test_unrelated_guides_are_not_injected() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        _write(root / "厨师" / "guide.md", "## 调料架\n调料架食盐大蒜。")
        state = StubState(current_reward_options=[{"name": "南瓜"}])

        context = retrieve_guides_for_ai(
            data=_data(),
            state=state,
            build_analysis={},
            recommendations=[],
            guides_dir=root,
        )

    assert context == []


def test_missing_guides_directory_falls_back_to_empty_context() -> None:
    state = StubState(current_reward_options=[{"name": "南瓜"}])

    context = retrieve_guides_for_ai(
        data=_data(),
        state=state,
        build_analysis={},
        recommendations=[],
        guides_dir=Path("Z:/definitely/missing/guides"),
    )

    assert context == []


def test_ai_prompt_includes_only_retrieved_sections() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "guides"
        _write(
            root / "厨师" / "guide.md",
            """## 南瓜路线
南瓜、加热箱和减速火。

## 整篇里无关章节
这段不应该进入 Prompt。
""",
        )
        state = StubState(current_reward_options=[{"name": "南瓜"}])
        hits = search_guides(
            query=build_guide_query(
                data=_data(),
                state=state,
                build_analysis={},
                recommendations=[],
            ),
            guides_dir=root,
        )
        guide_context = guide_hits_to_prompt_context(hits)

    payload = compact_recommendations(
        data=_data(),
        hero="Jules",
        build_name="ChefBuild",
        current_day=6,
        owned_cards={},
        results=[],
        guide_context=guide_context,
    )
    messages = build_ai_messages(payload)
    serialized = json.dumps(messages, ensure_ascii=False)

    assert "相关攻略" in json.dumps(payload, ensure_ascii=False)
    assert "南瓜、加热箱和减速火" in serialized
    assert "整篇里无关章节" not in serialized


def test_guide_injection_does_not_change_rule_recommendations() -> None:
    web_app.ANALYSIS_CACHE.clear()
    web_app.AI_PAYLOAD_CACHE.clear()
    web_app.AI_ANALYSIS_CACHE.clear()
    data = load_all_data(DATA_DIR)
    payload = {
        "source": "bepinex",
        "hero": "Vanessa",
        "day": 6,
        "event_options": ["Colt", "Kina"],
        "owned_cards": [{"name": "Ballista", "rarity": "gold"}],
    }

    base = web_app.analyze_payload(data, payload, top=2)
    with (
        patch.object(
            web_app,
            "retrieve_guides_for_ai",
            return_value=[
                {
                    "章节标题": "测试攻略",
                    "来源": "通用/test.md",
                    "赛季": "S16",
                    "命中原因": ["测试"],
                    "正文": "只作为 AI 上下文。",
                }
            ],
        ),
        patch.object(web_app, "analyze_with_ai", return_value="ok"),
    ):
        augmented = web_app.analyze_payload(data, payload, top=2, include_ai=True)

    assert augmented["ai_analysis"] == "ok"
    assert augmented["recommendations"] == base["recommendations"]
