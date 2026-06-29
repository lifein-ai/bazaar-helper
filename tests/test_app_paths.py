from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from app_paths import get_runtime_dir


def test_runtime_dir_uses_local_app_data() -> None:
    with patch.dict(
        "os.environ",
        {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"},
        clear=True,
    ):
        assert get_runtime_dir() == (
            Path(r"C:\Users\Test\AppData\Local") / "BazaarHelper" / "runtime"
        )


def test_runtime_dir_override_takes_priority(tmp_path: Path) -> None:
    custom_dir = tmp_path / "portable-runtime"
    with patch.dict(
        "os.environ",
        {
            "LOCALAPPDATA": r"C:\Users\Test\AppData\Local",
            "BAZAAR_HELPER_DATA_DIR": str(custom_dir),
        },
        clear=True,
    ):
        assert get_runtime_dir() == custom_dir.resolve()
