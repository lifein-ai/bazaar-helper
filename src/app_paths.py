from __future__ import annotations

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