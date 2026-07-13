from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import update_manager


def write_release_zip(path: Path, version: str = "0.2.0") -> None:
    version_info = {"name": "BazaarHelper", "version": version}
    files = {
        "BazaarHelper.exe": b"exe",
        "_internal/runtime.txt": b"internal",
        "data/events.json": b"{}",
        "data/cards_generated.json": b"{}",
        "guides/Jules/.gitkeep": b"",
        "VERSION": version.encode("utf-8"),
        "version.json": json.dumps(version_info).encode("utf-8"),
        "start.bat": b"start",
        "update_url.txt": b"https://example.com/latest.json",
        "update_helper.ps1": b"helper",
        "install_update.bat": b"install",
    }
    with zipfile.ZipFile(path, "w") as package:
        for name, content in files.items():
            package.writestr(name, content)


def test_version_compare_is_numeric() -> None:
    assert update_manager.compare_versions("2.10.0", "2.9.9") > 0
    assert update_manager.compare_versions("v2.3", "2.3.0") == 0
    assert update_manager.compare_versions("2.3.0-beta", "2.3.0") < 0


def test_manifest_accepts_quark_download_url() -> None:
    info = update_manager.normalize_manifest(
        {
            "version": "2.3.0",
            "download_url": "https://pan.quark.cn/s/example",
            "changelog": ["新增功能", "修复问题"],
            "force_update": False,
        }
    )

    assert info.version == "2.3.0"
    assert info.download_url.startswith("https://pan.quark.cn/")
    assert info.changelog == ["新增功能", "修复问题"]


def test_manifest_url_can_be_configured_by_release_file(tmp_path: Path) -> None:
    url_file = tmp_path / "update_url.txt"
    url_file.write_text("https://example.com/latest.json\n", encoding="utf-8")

    with patch.dict("os.environ", {"BAZAAR_HELPER_UPDATE_MANIFEST_URL": ""}):
        assert update_manager.resolve_manifest_url(tmp_path) == "https://example.com/latest.json"


def test_update_package_rejects_path_traversal(tmp_path: Path) -> None:
    package_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("../evil.txt", "bad")

    with pytest.raises(update_manager.UpdateError, match="路径穿越"):
        update_manager.validate_update_package(package_path)


def test_update_package_requires_release_structure(tmp_path: Path) -> None:
    package_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("VERSION", "0.2.0")

    with pytest.raises(update_manager.UpdateError, match="结构"):
        update_manager.validate_update_package(package_path)


def test_update_package_validates_expected_version(tmp_path: Path) -> None:
    package_path = tmp_path / "BazaarHelper-0.2.0.zip"
    write_release_zip(package_path, "0.2.0")
    expected = update_manager.UpdateInfo(
        version="0.3.0",
        download_url="https://pan.quark.cn/s/example",
        changelog=[],
    )

    with (
        patch.object(update_manager, "get_current_version", return_value="0.1.0"),
        pytest.raises(update_manager.UpdateError, match="远程配置版本"),
    ):
        update_manager.validate_update_package(package_path, expected)


def test_update_package_accepts_nested_release_root(tmp_path: Path) -> None:
    package_path = tmp_path / "BazaarHelper-0.2.0.zip"
    with zipfile.ZipFile(package_path, "w") as package:
        for name, content in {
            "BazaarHelper/BazaarHelper.exe": b"exe",
            "BazaarHelper/_internal/runtime.txt": b"internal",
            "BazaarHelper/data/events.json": b"{}",
            "BazaarHelper/data/cards_generated.json": b"{}",
            "BazaarHelper/guides/Jules/.gitkeep": b"",
            "BazaarHelper/VERSION": b"0.2.0",
            "BazaarHelper/version.json": b'{"version":"0.2.0"}',
            "BazaarHelper/start.bat": b"start",
            "BazaarHelper/update_url.txt": b"https://example.com/latest.json",
            "BazaarHelper/update_helper.ps1": b"helper",
            "BazaarHelper/install_update.bat": b"install",
        }.items():
            package.writestr(name, content)

    with patch.object(update_manager, "get_current_version", return_value="0.1.0"):
        info = update_manager.validate_update_package(package_path)

    assert info.version == "0.2.0"
    assert info.payload_root == "BazaarHelper"
