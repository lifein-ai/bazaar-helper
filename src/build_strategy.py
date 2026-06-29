from __future__ import annotations

from typing import Any


GAME_STAGE_RANGES = {
    "early": (1, 5),
    "mid": (6, 9),
    "late": (10, None),
}

STAGE_LABELS_ZH = {
    "early": "前期",
    "mid": "中期",
    "late": "后期",
}


def get_game_stage_for_day(current_day: int) -> str:
    for stage, (start_day, end_day) in GAME_STAGE_RANGES.items():
        if current_day >= start_day and (end_day is None or current_day <= end_day):
            return stage

    raise ValueError(f"天数必须是正整数：{current_day}")


def build_applies_to_stage(build_data: dict[str, Any], stage: str) -> bool:
    applicable_stages = build_data.get("applicable_stages", [])
    return stage in applicable_stages


def build_applies_to_day(build_data: dict[str, Any], current_day: int) -> bool:
    day_range = build_data.get("day_range")
    if day_range and len(day_range) == 2:
        start_day, end_day = day_range
        return current_day >= start_day and (end_day is None or current_day <= end_day)

    return build_applies_to_stage(build_data, get_game_stage_for_day(current_day))


def applicable_build_names(
    builds: dict[str, Any],
    hero: str,
    current_day: int,
) -> list[str]:
    return [
        build_name
        for build_name, build_data in builds.items()
        if build_data.get("hero") == hero and build_applies_to_day(build_data, current_day)
    ]


def format_build_timing_summary(build_data: dict[str, Any], current_day: int) -> str:
    current_stage = get_game_stage_for_day(current_day)
    applicable_stages = build_data.get("applicable_stages", [])
    day_range = build_data.get("day_range")
    current_stage_text = STAGE_LABELS_ZH.get(current_stage, current_stage)
    stage_text = (
        "、".join(STAGE_LABELS_ZH.get(stage, stage) for stage in applicable_stages)
        if applicable_stages
        else "未设置"
    )

    if day_range and len(day_range) == 2:
        start_day, end_day = day_range
        day_text = f"第 {start_day} 天后" if end_day is None else f"第 {start_day}-{end_day} 天"
    else:
        day_text = "未设置天数范围"

    status = "适合当前天数" if build_applies_to_day(build_data, current_day) else "不适合当前天数"
    return f"当前阶段={current_stage_text}；阵容阶段={stage_text}；{day_text}；{status}"
