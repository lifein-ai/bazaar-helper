from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from app_paths import get_runtime_dir  # noqa: E402


runtime_dir = get_runtime_dir()
path = Path(sys.argv[1]) if len(sys.argv) > 1 else runtime_dir / "cache_diagnostics.json"
out_path = runtime_dir / "cache_candidates.txt"

data = json.loads(path.read_text(encoding="utf-8-sig"))

important = re.compile(
    r"TheBazaar|Bazaar|ClientCache|RunConfiguration|CardMap|CardTemplate|"
    r"GetCardTemplate|GetCardById|HasStaticCardTemplate|loadedCards|"
    r"validCards|StaticCard|Karnok|Karnock|CardDefinition|CardData|ItemData",
    re.IGNORECASE,
)

noise = re.compile(
    r"TMPro|TextMeshPro|UnityEngine\.UI|UnityEngine\.RectTransform|"
    r"UnityEngine\.Transform|System\.Int32|System\.Boolean|System\.Single|"
    r"Microsoft|Newtonsoft|BepInEx|Harmony|MonoMod",
    re.IGNORECASE,
)


def walk(value: Any, path_text: str = "$"):
    if isinstance(value, dict):
        yield path_text, value
        for key, child in value.items():
            yield from walk(child, f"{path_text}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path_text}[{index}]")


def score_text(text: str) -> int:
    score = 0
    strong_keywords = [
        "TheBazaar",
        "ClientCache",
        "RunConfiguration",
        "CardMap",
        "CardTemplate",
        "GetCardTemplate",
        "loadedCards",
        "Karnok",
        "StaticCard",
    ]

    lower = text.lower()

    for keyword in strong_keywords:
        if keyword.lower() in lower:
            score += 10

    if "card" in lower:
        score += 2
    if "template" in lower:
        score += 2
    if "cache" in lower:
        score += 2
    if "dictionary" in lower or "list" in lower:
        score += 1

    if noise.search(text):
        score -= 3

    return score


matches: list[tuple[int, str, str]] = []

for node_path, node in walk(data):
    text = json.dumps(node, ensure_ascii=False)
    if not important.search(text):
        continue

    score = score_text(text)
    if score <= 0:
        continue

    if len(text) > 1200:
        text = text[:1200] + "...<truncated>"

    matches.append((score, node_path, text))

matches.sort(key=lambda item: item[0], reverse=True)

lines: list[str] = []
lines.append(f"source: {path}")
lines.append(f"matches: {len(matches)}")
lines.append("")

for index, (score, node_path, text) in enumerate(matches[:120], start=1):
    lines.append("=" * 80)
    lines.append(f"#{index} score={score}")
    lines.append(f"path={node_path}")
    lines.append(text)
    lines.append("")

out_path.write_text("\n".join(lines), encoding="utf-8")

print(f"wrote {out_path}")
print(f"matches: {len(matches)}")
print(f"open {out_path}")
