from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_root() -> Path:
    """
    源码运行：
        项目根目录

    PyInstaller 打包后：
        exe 所在目录
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def get_runtime_dir() -> Path:
    """Return a stable per-user directory independent of the app location."""
    override = os.environ.get("BAZAAR_HELPER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "BazaarHelper" / "runtime"

    return Path.home() / ".bazaar_helper" / "runtime"
