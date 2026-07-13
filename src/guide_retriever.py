from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app_paths import get_app_root
from build_strategy import get_game_stage_for_day


DEFAULT_GUIDES_DIR = get_app_root() / "guides"
COMMON_GUIDE_DIR_NAMES = {"Common", "common", "通用"}
METADATA_SECTION_TITLES = {"文档信息", "攻略范围"}
MAX_GUIDE_CONTEXT_CHARS = 9000
MAX_SECTION_BODY_CHARS = 2200
MIN_SECTION_SCORE = 4.0

HERO_DIR_ALIASES = {
    "Jules": {"Jules", "朱尔斯", "厨师"},
    "Vanessa": {"Vanessa", "瓦内莎", "海盗"},
    "Pygmalien": {"Pygmalien", "皮格马利翁", "猪猪"},
    "Mak": {"Mak", "马克", "炼金", "炼金术士"},
    "Dooley": {"Dooley", "杜利"},
    "Karnok": {"Karnok", "卡诺克"},
}

STAGE_TERMS_ZH = {
    "early": {"前期", "开局", "早期"},
    "mid": {"中期", "中盘"},
    "late": {"后期", "终局", "大后期"},
}

STOP_TERMS = {
    "当前",
    "物品",
    "阵容",
    "成长",
    "事件",
    "选择",
    "推荐",
    "核心",
    "卡牌",
    "技能",
    "商店",
    "阶段",
    "build",
    "card",
    "cards",
    "core",
    "current",
    "event",
    "events",
    "guide",
    "item",
    "items",
    "shop",
    "skill",
}

TITLE_WEIGHTS = {
    "candidate": 9.0,
    "missing_core": 9.0,
    "build": 8.0,
    "owned_card": 6.0,
    "skill": 6.0,
    "event": 5.0,
    "hero": 3.0,
    "stage": 2.0,
}

BODY_WEIGHTS = {
    "candidate": 3.0,
    "missing_core": 3.0,
    "build": 2.5,
    "owned_card": 2.0,
    "skill": 2.0,
    "event": 1.5,
    "hero": 0.5,
    "stage": 0.5,
}


@dataclass(frozen=True)
class GuideSection:
    title: str
    body: str
    source_file: str
    hero: str
    season: str | None = None
    version_context: str | None = None


@dataclass(frozen=True)
class GuideEntity:
    text: str
    kind: str


@dataclass(frozen=True)
class GuideQuery:
    hero: str
    day: int | None = None
    season: str | None = None
    entities: tuple[GuideEntity, ...] = ()
    allowed_heroes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GuideHit:
    section: GuideSection
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class GuideCorpus:
    signature: tuple[tuple[str, int, int], ...]
    sections: tuple[GuideSection, ...]


_CORPUS_CACHE: dict[Path, GuideCorpus] = {}


def retrieve_guides_for_ai(
    *,
    data: dict[str, Any],
    state: Any,
    build_analysis: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    guides_dir: Path | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    guides_dir = guides_dir or DEFAULT_GUIDES_DIR
    try:
        query = build_guide_query(
            data=data,
            state=state,
            build_analysis=build_analysis,
            recommendations=recommendations,
        )
        hits = search_guides(query=query, guides_dir=guides_dir, limit=limit)
        context = guide_hits_to_prompt_context(hits)
        _log_retrieval(query=query, hits=hits, injected_count=len(context))
        return context
    except Exception as exc:  # noqa: BLE001 - guide lookup must never block AI.
        print(f"[guide-retrieval] failed, fallback to original AI flow: {exc}")
        return []


def build_guide_query(
    *,
    data: dict[str, Any],
    state: Any,
    build_analysis: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
) -> GuideQuery:
    hero = str(getattr(state, "hero", "") or "")
    day = getattr(state, "day", None)
    translations = data.get("translations", {}).get("by_name", {})
    entities: dict[str, GuideEntity] = {}

    def add(value: Any, kind: str) -> None:
        for term in _expand_display_terms(value, translations):
            normalized = _normalize_term(term)
            if not _is_usable_term(term, normalized):
                continue
            existing = entities.get(normalized)
            if existing is None or _kind_rank(kind) > _kind_rank(existing.kind):
                entities[normalized] = GuideEntity(text=term, kind=kind)

    add(hero, "hero")
    for alias in _hero_aliases(hero, translations):
        add(alias, "hero")

    if day is not None:
        try:
            stage = get_game_stage_for_day(int(day))
        except (TypeError, ValueError):
            stage = ""
        for stage_term in STAGE_TERMS_ZH.get(stage, set()):
            add(stage_term, "stage")
        if stage:
            add(stage, "stage")

    build_name = str(getattr(state, "build", "") or "")
    build_data = data.get("builds", {}).get(build_name, {})
    add(build_name, "build")
    if isinstance(build_data, dict):
        add(build_data.get("name"), "build")
        add(build_data.get("display_name"), "build")

    owned_cards = getattr(state, "owned_cards", {}) or {}
    if isinstance(owned_cards, dict):
        for name in owned_cards:
            add(name, "owned_card")

    for collection_name in ("owned_items", "board_items", "stash_items", "skills"):
        for item in _as_dict_list(getattr(state, collection_name, None)):
            kind = "skill" if collection_name == "skills" else "owned_card"
            add(_entry_name(item), kind)

    for event_name in getattr(state, "event_options", []) or []:
        add(event_name, "event")

    for recommendation in recommendations or []:
        add(recommendation.get("event_name"), "event")
        add(recommendation.get("best_followup"), "event")
        add(recommendation.get("best_followup_display"), "event")
        for child in recommendation.get("child_options", []) or []:
            if isinstance(child, dict):
                add(child.get("name"), "event")
                add(child.get("display_name"), "event")
                add(child.get("source_name"), "event")
                add(child.get("description"), "event")
        for card in recommendation.get("possible_cards", [])[:8]:
            if isinstance(card, dict) and card.get("role") in {
                "core",
                "transition",
                "optional",
            }:
                add(card.get("name"), "candidate")

    for item in _candidate_entries_from_state(state):
        add(_entry_name(item), "candidate")

    for candidate in (build_analysis or {}).get("candidate_cards", []) or []:
        if isinstance(candidate, dict):
            add(candidate.get("card_name"), "candidate")
            add(candidate.get("card_display_name"), "candidate")

    for match in (build_analysis or {}).get("best_matching_builds", []) or []:
        if not isinstance(match, dict):
            continue
        add(match.get("name") or match.get("build_id"), "build")
        for name in match.get("missing_core", []) or []:
            add(name, "missing_core")
        for name in match.get("missing_core_display", []) or []:
            add(name, "missing_core")
        for name in match.get("owned_core", []) or []:
            add(name, "owned_card")
        for name in match.get("owned_core_display", []) or []:
            add(name, "owned_card")

    return GuideQuery(
        hero=hero,
        day=int(day) if isinstance(day, int) else None,
        entities=tuple(entities.values()),
        allowed_heroes=frozenset(_allowed_guide_heroes(hero, translations)),
    )


def search_guides(
    *,
    query: GuideQuery,
    guides_dir: Path | None = None,
    limit: int = 5,
    min_score: float = MIN_SECTION_SCORE,
) -> list[GuideHit]:
    guides_dir = (guides_dir or DEFAULT_GUIDES_DIR).resolve()
    if not guides_dir.exists() or not guides_dir.is_dir():
        print(f"[guide-retrieval] guides dir unavailable: {guides_dir}")
        return []

    corpus = load_guide_corpus(guides_dir)
    if not corpus.sections:
        return []

    hits: list[GuideHit] = []
    for section in corpus.sections:
        if not _section_allowed_for_query(section, query):
            continue
        score, reasons = score_section(section, query)
        if score >= min_score:
            hits.append(GuideHit(section=section, score=score, reasons=tuple(reasons)))

    hits.sort(
        key=lambda hit: (
            -hit.score,
            hit.section.hero not in query.allowed_heroes,
            hit.section.source_file,
            hit.section.title,
        )
    )
    return hits[: max(limit, 0)]


def guide_cache_marker(guides_dir: Path | None = None) -> str:
    guides_dir = (guides_dir or DEFAULT_GUIDES_DIR).resolve()
    if not guides_dir.exists() or not guides_dir.is_dir():
        return "missing"
    return repr(_guides_signature(guides_dir))


def load_guide_corpus(guides_dir: Path) -> GuideCorpus:
    guides_dir = guides_dir.resolve()
    signature = _guides_signature(guides_dir)
    cached = _CORPUS_CACHE.get(guides_dir)
    if cached is not None and cached.signature == signature:
        return cached

    sections: list[GuideSection] = []
    for relative, _, _ in signature:
        path = guides_dir / relative
        try:
            sections.extend(parse_guide_file(path, guides_dir))
        except Exception as exc:  # noqa: BLE001 - one bad guide should not block all guides.
            print(f"[guide-retrieval] failed to read guide {path}: {exc}")

    corpus = GuideCorpus(signature=signature, sections=tuple(sections))
    _CORPUS_CACHE[guides_dir] = corpus
    return corpus


def parse_guide_file(path: Path, guides_dir: Path | None = None) -> list[GuideSection]:
    guides_dir = (guides_dir or path.parent).resolve()
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    if not matches:
        return []

    relative = _safe_relative(path, guides_dir)
    hero = _hero_from_path(path, guides_dir)
    season = _extract_season(path.name)
    version_context: str | None = None
    searchable_sections: list[GuideSection] = []

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if title in METADATA_SECTION_TITLES:
            season = season or _extract_season(body)
            version_context = version_context or _extract_version_context(body)
            continue

        searchable_sections.append(
            GuideSection(
                title=title,
                body=body,
                source_file=relative,
                hero=hero,
                season=season,
                version_context=version_context,
            )
        )

    return searchable_sections


def score_section(section: GuideSection, query: GuideQuery) -> tuple[float, list[str]]:
    title = _normalize_for_match(section.title)
    body = _normalize_for_match(section.body)
    score = 0.0
    reasons: list[str] = []
    seen_terms: set[tuple[str, str]] = set()

    for entity in query.entities:
        normalized = _normalize_term(entity.text)
        key = (normalized, entity.kind)
        if key in seen_terms:
            continue
        seen_terms.add(key)
        if not normalized:
            continue

        if normalized in title:
            score += TITLE_WEIGHTS.get(entity.kind, 1.0)
            reasons.append(f"标题命中{_kind_label(entity.kind)}：{entity.text}")
        elif normalized in body:
            score += BODY_WEIGHTS.get(entity.kind, 0.5)
            reasons.append(f"正文命中{_kind_label(entity.kind)}：{entity.text}")

    if query.season and section.season and query.season == section.season:
        score += 2.0
        reasons.append(f"赛季一致：{section.season}")

    return score, reasons


def guide_hits_to_prompt_context(
    hits: list[GuideHit],
    *,
    max_total_chars: int = MAX_GUIDE_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    used_chars = 0
    seen_sections: set[tuple[str, str, str]] = set()

    for hit in hits:
        body = _fit_section_body(hit.section.body)
        section_key = (
            hit.section.source_file,
            hit.section.title,
            _normalize_for_match(body[:500]),
        )
        if section_key in seen_sections:
            continue
        seen_sections.add(section_key)
        item = {
            "章节标题": hit.section.title,
            "来源": hit.section.source_file,
            "英雄": hit.section.hero,
            "赛季": hit.section.season or "未标注",
            "版本背景": hit.section.version_context or "未标注",
            "命中原因": list(hit.reasons[:6]),
            "正文": body,
        }
        estimated_size = len(str(item))
        if context and used_chars + estimated_size > max_total_chars:
            break
        if not context and estimated_size > max_total_chars:
            item["正文"] = body[: max(800, max_total_chars // 2)].rstrip() + "\n\n（章节过长，已截断）"
            estimated_size = len(str(item))
        context.append(item)
        used_chars += estimated_size

    return context


def _guides_signature(guides_dir: Path) -> tuple[tuple[str, int, int], ...]:
    try:
        files = sorted(path for path in guides_dir.rglob("*.md") if path.is_file())
    except OSError:
        return ()

    signature: list[tuple[str, int, int]] = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((_safe_relative(path, guides_dir), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _hero_from_path(path: Path, guides_dir: Path) -> str:
    try:
        relative_parts = path.resolve().relative_to(guides_dir.resolve()).parts
    except ValueError:
        relative_parts = path.parts
    if len(relative_parts) >= 2:
        return relative_parts[0]
    return "通用"


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def _extract_season(text: str) -> str | None:
    match = re.search(r"\bS\s*(\d{1,2})\b", text, re.IGNORECASE)
    if match:
        return f"S{match.group(1)}"
    return None


def _extract_version_context(text: str) -> str | None:
    fallback: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip(" -*：:")
        if not line:
            continue
        if any(marker in line for marker in ("版本", "背景")):
            return line[:160]
        if fallback is None and "赛季" in line:
            fallback = line[:160]
    return fallback


def _section_allowed_for_query(section: GuideSection, query: GuideQuery) -> bool:
    if not query.allowed_heroes:
        return section.hero in COMMON_GUIDE_DIR_NAMES
    return section.hero in query.allowed_heroes or section.hero in COMMON_GUIDE_DIR_NAMES


def _allowed_guide_heroes(hero: str, translations: dict[str, str]) -> set[str]:
    aliases = set(COMMON_GUIDE_DIR_NAMES)
    aliases.update(_hero_aliases(hero, translations))
    aliases.add(hero)
    return {alias for alias in aliases if alias}


def _hero_aliases(hero: str, translations: dict[str, str]) -> set[str]:
    aliases = set(HERO_DIR_ALIASES.get(hero, set()))
    translated = translations.get(hero)
    if translated:
        aliases.add(translated)
    return aliases


def _expand_display_terms(value: Any, translations: dict[str, str]) -> list[str]:
    if value in (None, ""):
        return []
    term = str(value).strip()
    if not term:
        return []
    terms = [term]
    translated = translations.get(term)
    if translated and translated != term:
        terms.append(str(translated))
    return terms


def _candidate_entries_from_state(state: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for shop_name in ("current_shop", "effective_shop"):
        shop = getattr(state, shop_name, None)
        if isinstance(shop, dict) and isinstance(shop.get("visible_items"), list):
            candidates.extend(
                item for item in shop["visible_items"] if isinstance(item, dict)
            )
    reward_options = getattr(state, "current_reward_options", None)
    if isinstance(reward_options, list):
        candidates.extend(item for item in reward_options if isinstance(item, dict))
    return candidates


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _entry_name(item: dict[str, Any]) -> Any:
    return (
        item.get("name")
        or item.get("display_name")
        or item.get("card_name")
        or item.get("internal_name")
    )


def _normalize_for_match(value: str) -> str:
    return _normalize_term(value)


def _normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _is_usable_term(term: str, normalized: str) -> bool:
    if not normalized or normalized in STOP_TERMS:
        return False
    stripped = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    if stripped in STOP_TERMS:
        return False
    if all(ord(char) < 128 for char in stripped):
        return len(stripped) >= 3
    return len(stripped) >= 2


def _kind_rank(kind: str) -> int:
    return {
        "missing_core": 7,
        "candidate": 6,
        "build": 5,
        "skill": 4,
        "owned_card": 3,
        "event": 2,
        "hero": 1,
        "stage": 0,
    }.get(kind, 0)


def _kind_label(kind: str) -> str:
    return {
        "build": "阵容",
        "candidate": "候选物品",
        "event": "候选事件",
        "hero": "英雄",
        "missing_core": "缺失核心",
        "owned_card": "已有卡牌",
        "skill": "技能",
        "stage": "阶段",
    }.get(kind, kind)


def _fit_section_body(body: str) -> str:
    if len(body) <= MAX_SECTION_BODY_CHARS:
        return body
    clipped = body[:MAX_SECTION_BODY_CHARS].rstrip()
    paragraph_break = clipped.rfind("\n\n")
    if paragraph_break >= 800:
        clipped = clipped[:paragraph_break].rstrip()
    return clipped + "\n\n（章节过长，已截断）"


def _log_retrieval(
    *,
    query: GuideQuery,
    hits: list[GuideHit],
    injected_count: int,
) -> None:
    entities = ", ".join(
        f"{entity.kind}:{entity.text}" for entity in query.entities[:30]
    )
    hit_summary = "; ".join(
        f"{hit.section.source_file}##{hit.section.title} ({hit.score:.1f}: {', '.join(hit.reasons[:3])})"
        for hit in hits[:5]
    )
    print(
        "[guide-retrieval] "
        f"hero={query.hero or 'unknown'} "
        f"entities=[{entities}] "
        f"hits=[{hit_summary}] "
        f"injected={injected_count}"
    )
