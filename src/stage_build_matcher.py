from __future__ import annotations

from typing import Any

from build_strategy import build_applicable_stages, build_phase_relation, get_game_stage_for_day
from dooley_rules import dooley_missing_unobtainable_core_cards


PHASES = ("early", "mid", "late")
IMPORTANCE_RANK = {
    "ignored": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def analyze_stage_builds(
    *,
    data: dict[str, Any],
    hero: str,
    day: int,
    owned_cards: set[str],
    candidates: list[dict[str, Any]],
    gold: int | None,
    prestige: int | None,
    inventory_slots_used: int | None,
    inventory_slots_total: int | None,
    current_shop: dict[str, Any] | None,
) -> dict[str, Any]:
    current_phase = get_game_stage_for_day(day)
    builds = [
        normalize_build(build_id, raw)
        for build_id, raw in data.get("builds", {}).items()
        if isinstance(raw, dict) and raw.get("hero") in (None, hero)
    ]
    build_matches = [
        match_build(
            build,
            owned_cards,
            current_phase,
            current_day=day,
            cards=data.get("cards", {}),
        )
        for build in builds
    ]
    match_by_id = {item["build_id"]: item for item in build_matches}
    candidate_results = [
        evaluate_candidate(
            candidate=candidate,
            builds=builds,
            build_matches=match_by_id,
            current_phase=current_phase,
            data=data,
            gold=gold,
            prestige=prestige,
            inventory_slots_used=inventory_slots_used,
            inventory_slots_total=inventory_slots_total,
            fallback_price=estimated_avg_item_price(current_shop),
        )
        for candidate in candidates
        if candidate.get("name")
    ]
    bundles = visible_core_bundles(
        candidates=candidate_results,
        build_matches=match_by_id,
        owned_cards=owned_cards,
        gold=gold,
        prestige=prestige,
        inventory_slots_used=inventory_slots_used,
        inventory_slots_total=inventory_slots_total,
    )
    urgency = operation_urgency(
        build_matches=build_matches,
        prestige=prestige,
        gold=gold,
        inventory_slots_used=inventory_slots_used,
        inventory_slots_total=inventory_slots_total,
    )
    shop_action = decide_shop_action(
        candidate_results,
        bundles,
        current_shop,
        gold,
    )
    ranked_matches = sorted(
        [
            match
            for match in build_matches
            if match["relation"] not in {"past_build", "blocked_build"}
        ],
        key=lambda item: (
            -IMPORTANCE_RANK[item["importance"]],
            -match_band_rank(item["match_band"]),
            -float(item.get("core_completion_ratio", 0.0)),
            -int(item.get("owned_core_count", 0)),
            int(item.get("core_total", 0)),
            item["build_id"],
        ),
    )
    return {
        "current_phase": current_phase,
        "best_matching_builds": ranked_matches[:3],
        "build_matches": build_matches,
        "candidate_cards": candidate_results,
        "visible_core_bundles": bundles,
        "operation_urgency": urgency,
        **shop_action,
    }


def normalize_build(build_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    core = unique_strings(raw.get("core_cards"))
    explicit_optional = unique_strings(raw.get("optional_cards"))
    phase = str(raw.get("phase") or "").lower()
    legacy_non_core = (
        explicit_optional
        if phase in PHASES
        else unique_strings(
            list(raw.get("transition_cards") or [])
            + explicit_optional
        )
    )
    stages = build_applicable_stages(raw)
    if phase not in PHASES:
        phase = stages[0] if stages else phase_from_day_range(raw.get("day_range"))
    return {
        "build_id": str(raw.get("build_id") or build_id),
        "hero": raw.get("hero"),
        "name": str(raw.get("name") or raw.get("display_name") or build_id),
        "phase": phase if phase in PHASES else "early",
        "applicable_stages": stages,
        "core_cards": core,
        "optional_cards": [name for name in legacy_non_core if name not in core],
    }


def match_build(
    build: dict[str, Any],
    owned_cards: set[str],
    current_phase: str,
    current_day: int | None = None,
    cards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    core = build["core_cards"]
    optional = build["optional_cards"]
    owned_core = [name for name in core if name in owned_cards]
    missing_core = [name for name in core if name not in owned_cards]
    owned_optional = [name for name in optional if name in owned_cards]
    relation = build_phase_relation(build, current_phase)
    blocked_missing_core = dooley_missing_unobtainable_core_cards(
        hero=str(build.get("hero") or ""),
        current_day=current_day or 1,
        core_cards=core,
        owned_cards=owned_cards,
        cards=cards,
    )
    band = build_match_band(len(owned_core), len(core), len(owned_optional))
    core_total = len(core)
    owned_core_count = len(owned_core)
    core_completion_ratio = owned_core_count / core_total if core_total else 0.0
    reasons: list[str] = []
    if blocked_missing_core:
        importance = "ignored"
        band = "none"
        relation = "blocked_build"
        reasons.append(
            "Dooley core event has passed; missing event-only core card(s): "
            + ", ".join(blocked_missing_core)
            + "."
        )
    elif relation == "past_build":
        importance = "ignored"
        band = "none"
        reasons.append("该阵容已经过期，默认不再提供当前规划价值。")
    elif relation == "current_build":
        importance = "high" if owned_core else ("medium" if owned_optional else "low")
        reasons.append("该阵容属于当前阶段。")
    elif relation in {"future_build", "late_build"}:
        importance = "medium" if owned_core else "low"
        reasons.append("该阵容属于未来阶段，只作为后续方向。")
    else:
        importance = "low"
    if owned_core:
        reasons.append(f"已拥有核心卡：{', '.join(owned_core)}。")
    if band == "close" and missing_core:
        importance = max_importance(importance, "high")
        reasons.append(f"阵容已接近成型，仍缺核心：{', '.join(missing_core)}。")
    return {
        "build_id": build["build_id"],
        "name": build["name"],
        "phase": build["phase"],
        "applicable_stages": build["applicable_stages"],
        "owned_core": owned_core,
        "owned_core_count": owned_core_count,
        "core_total": core_total,
        "core_completion_ratio": round(core_completion_ratio, 4),
        "missing_core": missing_core,
        "owned_optional": owned_optional,
        "match_band": band,
        "importance": importance,
        "relation": relation,
        "reasons": reasons,
    }


def evaluate_candidate(
    *,
    candidate: dict[str, Any],
    builds: list[dict[str, Any]],
    build_matches: dict[str, dict[str, Any]],
    current_phase: str,
    data: dict[str, Any],
    gold: int | None,
    prestige: int | None,
    inventory_slots_used: int | None,
    inventory_slots_total: int | None,
    fallback_price: float | None = None,
) -> dict[str, Any]:
    card_name = str(candidate["name"])
    hits: list[dict[str, Any]] = []
    for build in builds:
        role = (
            "core" if card_name in build["core_cards"]
            else "optional" if card_name in build["optional_cards"]
            else None
        )
        if role is None:
            continue
        match = build_matches.get(build["build_id"], {})
        hits.append(
            {
                "build_id": build["build_id"],
                "build_name": build["name"],
                "build_phase": build["phase"],
                "applicable_stages": build["applicable_stages"],
                "role": role,
                "relation": match.get("relation") or build_phase_relation(build, current_phase),
            }
        )

    active_hits = [
        hit
        for hit in hits
        if hit["relation"] not in {"past_build", "blocked_build"}
    ]
    reasons: list[str] = []
    risks: list[str] = []
    needs_ai = False
    importance = "low"
    recommendation = "observe"

    if not hits:
        reasons.append("该卡不在已维护阵容中；不判废，但不增加阵容匹配价值。")
    elif not active_hits:
        importance = "ignored"
        recommendation = "skip"
        reasons.append("只命中过去阶段阵容，默认忽略其阵容规划价值。")
    else:
        for hit in active_hits:
            match = build_matches[hit["build_id"]]
            relation = hit["relation"]
            role = hit["role"]
            if relation == "current_build" and role == "core":
                hit_importance = "critical" if match["match_band"] == "close" else "high"
                importance = max_importance(importance, hit_importance)
                recommendation = "buy_now"
                reasons.append(f"命中当前阶段阵容「{hit['build_name']}」核心卡。")
            elif relation == "current_build" and role == "optional":
                importance = max_importance(importance, "medium")
                if recommendation != "buy_now":
                    recommendation = "tempo_upgrade"
                reasons.append(f"命中当前阶段阵容「{hit['build_name']}」可选卡。")
            elif role == "core" and relation == "future_build":
                importance = max_importance(importance, "medium")
                if recommendation not in {"buy_now", "tempo_upgrade"}:
                    recommendation = "stash_future"
                reasons.append(f"命中下一阶段阵容「{hit['build_name']}」核心卡。")
            elif role == "core" and relation == "late_build":
                importance = max_importance(importance, "medium")
                if recommendation not in {"buy_now", "tempo_upgrade"}:
                    recommendation = "stash_future"
                reasons.append("这是后期核心，只是可屯候选，不等于当前必买。")

    price = candidate_price(candidate, data, fallback_price)
    space_known = inventory_slots_used is not None and inventory_slots_total is not None
    space_available = (
        inventory_slots_used < inventory_slots_total if space_known else None
    )
    prestige_safe = prestige > 6 if prestige is not None else None
    affordable = gold >= price if gold is not None and price is not None else None

    if recommendation == "stash_future":
        if prestige_safe is False:
            recommendation = "observe"
            importance = min_importance(importance, "medium")
            risks.append("声望较低，不宜为未来卡牺牲当前生存。")
        if space_available is False:
            recommendation = "skip"
            risks.append("背包/棋盘空间已满。")
        if affordable is False:
            recommendation = "skip"
            risks.append("当前金币不足。")
        for known, label in (
            (prestige_safe, "声望安全性"),
            (space_available, "背包空间"),
            (affordable, "购买力"),
        ):
            if known is None:
                needs_ai = True
                risks.append(f"{label}未知，不能强推囤卡。")

    if len({hit["build_id"] for hit in active_hits}) >= 2:
        importance = max_importance(importance, "high")
        reasons.append("同一张卡同时命中多个当前/未来阵容，重要性提高。")

    return {
        "card_name": card_name,
        "build_hits": active_hits,
        "importance": importance,
        "recommendation_type": recommendation,
        "reasons": reasons,
        "risks": risks,
        "needs_ai_judgement": needs_ai,
        "price": price,
        "affordable": affordable,
    }


def visible_core_bundles(
    *,
    candidates: list[dict[str, Any]],
    build_matches: dict[str, dict[str, Any]],
    owned_cards: set[str],
    gold: int | None,
    prestige: int | None,
    inventory_slots_used: int | None,
    inventory_slots_total: int | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        for hit in candidate["build_hits"]:
            if hit["role"] == "core" and hit["relation"] in {
                "current_build", "future_build", "late_build"
            }:
                grouped.setdefault(hit["build_id"], []).append(candidate)
    result = []
    for build_id, cards in grouped.items():
        unique = list(dict.fromkeys(card["card_name"] for card in cards))
        if len(unique) < 2:
            continue
        match = build_matches[build_id]
        prices = [card.get("price") for card in cards]
        total_price = sum(prices) if all(price is not None for price in prices) else None
        affordable = gold >= total_price if gold is not None and total_price is not None else None
        constrained = (
            prestige is not None and prestige <= 6
        ) or (
            inventory_slots_used is not None
            and inventory_slots_total is not None
            and inventory_slots_used >= inventory_slots_total
        )
        importance = "critical" if match["match_band"] in {"developing", "close"} else "high"
        recommendation = (
            "consider_buying_together"
            if affordable is True and not constrained
            else "prioritize_best_core" if affordable is False
            else "unknown"
        )
        result.append(
            {
                "type": "visible_core_bundle",
                "build_id": build_id,
                "build_name": match["name"],
                "candidate_core_cards": unique,
                "owned_core_before": match["owned_core"],
                "owned_core_after_if_bought": list(
                    dict.fromkeys(match["owned_core"] + unique)
                ),
                "importance": importance,
                "affordable": affordable,
                "recommendation": recommendation,
                "reasons": ["当前实际候选中同时出现多张同阵容核心卡。"],
            }
        )
    return result


def operation_urgency(
    *,
    build_matches: list[dict[str, Any]],
    prestige: int | None,
    gold: int | None,
    inventory_slots_used: int | None,
    inventory_slots_total: int | None,
) -> dict[str, str]:
    close_missing = any(
        item["relation"] == "current_build"
        and item["match_band"] == "close"
        and item["missing_core"]
        for item in build_matches
    )
    space_tight = (
        inventory_slots_used >= inventory_slots_total
        if inventory_slots_used is not None and inventory_slots_total is not None
        else None
    )
    return {
        "survive_now": (
            "unknown" if prestige is None else "high" if prestige <= 5 else "medium" if prestige <= 9 else "low"
        ),
        "save_money": (
            "unknown" if gold is None else "high" if gold <= 3 else "medium" if gold <= 7 else "low"
        ),
        "stash_future": (
            "unknown" if prestige is None or space_tight is None
            else "low" if prestige <= 6 or space_tight
            else "medium"
        ),
        "find_core": "high" if close_missing else "low",
        "pivot": "low",
    }


def decide_shop_action(
    candidates: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
    current_shop: dict[str, Any] | None,
    gold: int | None,
) -> dict[str, Any]:
    if not isinstance(current_shop, dict):
        return {"shop_action": "unknown", "refresh_reason": "当前没有可靠商店状态。"}
    if any(item["importance"] in {"high", "critical"} for item in candidates):
        return {"shop_action": "buy_visible", "refresh_reason": "当前可见卡已有高重要性目标，不建议先刷新。"}
    if bundles:
        return {"shop_action": "consider_bundle", "refresh_reason": "当前可见卡已有核心组合，不建议先刷新。"}
    refresh_cost = current_shop.get("refresh_cost")
    available = current_shop.get("refresh_available")
    remaining = current_shop.get("refreshes_remaining")
    if available is False or remaining == 0:
        return {"shop_action": "skip", "refresh_reason": "当前不可刷新。"}
    if not isinstance(refresh_cost, int):
        return {"shop_action": "unknown", "refresh_reason": "刷新价格未知，不强推刷新。"}
    if gold is None:
        return {"shop_action": "unknown", "refresh_reason": "金币未知，无法判断刷新后购买力。"}
    if gold <= refresh_cost:
        return {"shop_action": "skip", "refresh_reason": "刷新后没有可靠购买预算。"}
    return {
        "shop_action": "unknown",
        "refresh_reason": "当前可见卡无高价值目标且金币可支付刷新，但缺少商店池匹配事实，不强推刷新。",
    }


def phase_relation(current: str, build_phase: str) -> str:
    return build_phase_relation({"phase": build_phase}, current)


def build_match_band(owned_core: int, core_total: int, owned_optional: int) -> str:
    if core_total and owned_core == core_total:
        return "locked"
    if core_total and owned_core >= 2 and core_total - owned_core == 1:
        return "close"
    if owned_core >= 2 or (core_total and owned_core * 2 >= core_total):
        return "developing"
    if owned_core or owned_optional:
        return "seed"
    return "none"


def match_band_rank(value: str) -> int:
    return {"none": 0, "seed": 1, "developing": 2, "close": 3, "locked": 4}.get(value, 0)


def candidate_price(
    candidate: dict[str, Any],
    data: dict[str, Any],
    fallback_price: float | None = None,
) -> float | None:
    live_price = candidate.get("price")
    if isinstance(live_price, (int, float)):
        return int(live_price)
    card = data.get("cards", {}).get(candidate.get("name"))
    rarity = str(candidate.get("rarity") or "").lower()
    if not isinstance(card, dict) or not rarity:
        return fallback_price
    price = (card.get("buy_prices") or {}).get(rarity)
    return int(price) if isinstance(price, (int, float)) else fallback_price


def estimated_avg_item_price(current_shop: dict[str, Any] | None) -> float | None:
    if not isinstance(current_shop, dict):
        return None
    value = current_shop.get("estimated_avg_item_price")
    return float(value) if isinstance(value, (int, float)) and value >= 0 else None


def phase_from_day_range(day_range: Any) -> str:
    if isinstance(day_range, list) and day_range:
        try:
            return get_game_stage_for_day(max(1, int(day_range[0])))
        except (TypeError, ValueError):
            pass
    return "early"


def unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if value))


def max_importance(left: str, right: str) -> str:
    return left if IMPORTANCE_RANK[left] >= IMPORTANCE_RANK[right] else right


def min_importance(left: str, right: str) -> str:
    return left if IMPORTANCE_RANK[left] <= IMPORTANCE_RANK[right] else right
