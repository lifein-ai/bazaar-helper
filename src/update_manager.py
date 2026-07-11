from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from app_paths import get_app_root, get_runtime_dir


APP_NAME = "BazaarHelper"
APP_EXE_NAME = "BazaarHelper.exe"
UPDATE_HELPER_NAME = "update_helper.ps1"
UPDATE_URL_FILE = "update_url.txt"
UPDATE_TIMEOUT_SECONDS = 5
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
REQUIRED_PACKAGE_PATHS = (
    APP_EXE_NAME,
    "_internal",
    "data",
    "data/events.json",
    "data/cards_generated.json",
    "VERSION",
    "version.json",
    "start.bat",
    UPDATE_HELPER_NAME,
    "install_update.bat",
)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    changelog: list[str]
    force_update: bool = False
    sha256: str | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "download_url": self.download_url,
            "changelog": self.changelog,
            "force_update": self.force_update,
            "sha256": self.sha256,
            "published_at": self.published_at,
        }


@dataclass(frozen=True)
class UpdatePackageInfo:
    path: Path
    version: str
    payload_root: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "payload_root": self.payload_root,
            "sha256": self.sha256,
        }


_UPDATE_LOCK = threading.Lock()
_UPDATE_CACHE: dict[str, Any] = {
    "status": "idle",
    "checked_at": None,
    "current_version": None,
    "update_available": False,
    "update": None,
    "error": None,
    "dismissed": False,
}
_UPDATE_THREAD: threading.Thread | None = None


def get_current_version(app_root: Path | None = None) -> str:
    root = app_root or get_app_root()
    version_json = root / "version.json"
    if version_json.exists():
        try:
            payload = json.loads(version_json.read_text(encoding="utf-8-sig"))
            version = str(payload.get("version", "")).strip()
            if version:
                return version
        except (OSError, ValueError, TypeError):
            pass

    version_file = root / "VERSION"
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8-sig").strip()
        if version:
            return version

    return "0.0.0"


def parse_version(value: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    clean = str(value or "").strip()
    match = re.match(r"^[vV]?(\d+(?:\.\d+){0,3})(?:[-+]([0-9A-Za-z.-]+))?$", clean)
    if not match:
        raise UpdateError(f"版本号格式无效：{value}")

    release = tuple(int(part) for part in match.group(1).split("."))
    release = release + (0,) * (4 - len(release))
    suffix_text = match.group(2) or ""
    suffix = tuple(suffix_text.split(".")) if suffix_text else ()
    return release, suffix


def compare_versions(left: str, right: str) -> int:
    left_release, left_suffix = parse_version(left)
    right_release, right_suffix = parse_version(right)
    if left_release != right_release:
        return 1 if left_release > right_release else -1
    if left_suffix == right_suffix:
        return 0
    if not left_suffix:
        return 1
    if not right_suffix:
        return -1
    return 1 if left_suffix > right_suffix else -1


def resolve_manifest_url(app_root: Path | None = None) -> str:
    env_url = os.environ.get("BAZAAR_HELPER_UPDATE_MANIFEST_URL", "").strip()
    if env_url:
        return env_url

    url_file = (app_root or get_app_root()) / UPDATE_URL_FILE
    if url_file.exists():
        return url_file.read_text(encoding="utf-8-sig").strip()

    return ""


def normalize_manifest(payload: Any) -> UpdateInfo:
    if not isinstance(payload, dict):
        raise UpdateError("远程更新配置必须是 JSON 对象。")

    version = str(payload.get("version", "")).strip()
    download_url = str(
        payload.get("download_url")
        or payload.get("quark_url")
        or payload.get("url")
        or ""
    ).strip()
    if not version or not download_url:
        raise UpdateError("远程更新配置缺少 version 或 download_url。")
    parse_version(version)

    raw_changelog = payload.get("changelog", payload.get("notes", []))
    if isinstance(raw_changelog, str):
        changelog = [line.strip() for line in raw_changelog.splitlines() if line.strip()]
    elif isinstance(raw_changelog, list):
        changelog = [str(item).strip() for item in raw_changelog if str(item).strip()]
    else:
        changelog = []

    sha256 = payload.get("sha256") or payload.get("package_sha256")
    sha256_text = str(sha256).strip().lower() if sha256 else None
    if sha256_text and not re.fullmatch(r"[0-9a-f]{64}", sha256_text):
        raise UpdateError("远程更新配置中的 sha256 格式无效。")

    return UpdateInfo(
        version=version,
        download_url=download_url,
        changelog=changelog,
        force_update=bool(payload.get("force_update", False)),
        sha256=sha256_text,
        published_at=str(payload.get("published_at", "")).strip() or None,
    )


def fetch_update_info(manifest_url: str) -> UpdateInfo:
    request = Request(manifest_url, headers={"User-Agent": f"{APP_NAME}/update-check"})
    try:
        with urlopen(request, timeout=UPDATE_TIMEOUT_SECONDS) as response:
            raw = response.read(1024 * 1024)
    except (OSError, URLError) as exc:
        raise UpdateError(f"读取远程更新配置失败：{exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"远程更新配置不是有效 JSON：{exc}") from exc

    return normalize_manifest(payload)


def check_for_updates() -> dict[str, Any]:
    manifest_url = resolve_manifest_url()
    current_version = get_current_version()
    result: dict[str, Any] = {
        "status": "ok",
        "checked_at": time.time(),
        "current_version": current_version,
        "manifest_url_configured": bool(manifest_url),
        "update_available": False,
        "update": None,
        "error": None,
    }

    if not manifest_url:
        result["status"] = "not_configured"
        return result

    update_info = fetch_update_info(manifest_url)
    if compare_versions(update_info.version, current_version) > 0:
        result["update_available"] = True
        result["update"] = update_info.to_dict()
    return result


def start_background_update_check(force: bool = False) -> None:
    global _UPDATE_THREAD
    with _UPDATE_LOCK:
        if (
            not force
            and _UPDATE_THREAD is not None
            and _UPDATE_THREAD.is_alive()
        ):
            return
        _UPDATE_CACHE["status"] = "checking"
        _UPDATE_CACHE["error"] = None
        _UPDATE_THREAD = threading.Thread(target=_background_check, daemon=True)
        _UPDATE_THREAD.start()


def _background_check() -> None:
    try:
        result = check_for_updates()
    except Exception as exc:  # noqa: BLE001 - update check must never stop app startup.
        result = {
            "status": "error",
            "checked_at": time.time(),
            "current_version": get_current_version(),
            "update_available": False,
            "update": None,
            "error": str(exc),
            "manifest_url_configured": bool(resolve_manifest_url()),
        }

    with _UPDATE_LOCK:
        dismissed = bool(_UPDATE_CACHE.get("dismissed"))
        _UPDATE_CACHE.update(result)
        _UPDATE_CACHE["dismissed"] = dismissed


def get_update_status(wait_seconds: float = 0.0) -> dict[str, Any]:
    thread = _UPDATE_THREAD
    if wait_seconds > 0 and thread is not None and thread.is_alive():
        thread.join(wait_seconds)
    with _UPDATE_LOCK:
        status = dict(_UPDATE_CACHE)
        if isinstance(status.get("update"), dict):
            status["update"] = dict(status["update"])
        return status


def dismiss_update_prompt() -> dict[str, Any]:
    with _UPDATE_LOCK:
        _UPDATE_CACHE["dismissed"] = True
        status = dict(_UPDATE_CACHE)
        if isinstance(status.get("update"), dict):
            status["update"] = dict(status["update"])
        return status


def open_download_page() -> None:
    status = get_update_status()
    update = status.get("update")
    if not isinstance(update, dict) or not update.get("download_url"):
        raise UpdateError("当前没有可打开的更新下载链接。")
    webbrowser.open(str(update["download_url"]), new=2, autoraise=True)


def common_download_dirs() -> list[Path]:
    candidates: list[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / "Downloads")
        candidates.append(Path(user_profile) / "OneDrive" / "Downloads")
    home = Path.home()
    candidates.append(home / "Downloads")

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen and resolved.exists() and resolved.is_dir():
            seen.add(key)
            result.append(resolved)
    return result


def find_update_package_candidates(limit: int = 10) -> list[dict[str, Any]]:
    expected = expected_update_info()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for directory in common_download_dirs():
        for path in directory.glob("*.zip"):
            try:
                stat = path.stat()
                info = validate_update_package(path, expected)
            except Exception:
                continue
            candidates.append(
                (
                    stat.st_mtime,
                    {
                        **info.to_dict(),
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                    },
                )
            )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:limit]]


def select_update_package_with_dialog() -> UpdatePackageInfo:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 - keep caller-facing message simple.
        raise UpdateError("无法打开文件选择窗口，请检查系统是否支持 Tk 文件对话框。") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial_dirs = common_download_dirs()
    try:
        selected = filedialog.askopenfilename(
            title="选择 BazaarHelper 更新包",
            initialdir=str(initial_dirs[0]) if initial_dirs else str(Path.home()),
            filetypes=[("Zip 更新包", "*.zip"), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()

    if not selected:
        raise UpdateError("已取消选择更新包。")

    return validate_update_package(Path(selected), expected_update_info())


def update_root() -> Path:
    root = get_runtime_dir().parent / "updates" / "manual"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_uploaded_package(
    stream: BinaryIO,
    content_length: int,
    filename: str,
) -> Path:
    if content_length <= 0:
        raise UpdateError("上传的更新包为空。")
    if content_length > MAX_UPLOAD_BYTES:
        raise UpdateError("更新包过大，已拒绝处理。")

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "update.zip").name)
    if not safe_name.lower().endswith(".zip"):
        safe_name = f"{safe_name}.zip"
    destination = update_root() / f"{int(time.time())}-{safe_name}"

    remaining = content_length
    with destination.open("wb") as target:
        while remaining > 0:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            target.write(chunk)
            remaining -= len(chunk)

    if remaining:
        destination.unlink(missing_ok=True)
        raise UpdateError("更新包上传不完整。")

    return destination


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise UpdateError(f"更新包包含不安全路径：{name}")
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        raise UpdateError(f"更新包包含路径穿越：{name}")
    return "/".join(parts)


def _find_payload_root(names: set[str]) -> str:
    candidates = [""]
    candidates.extend(
        sorted(
            {
                name.split("/", 1)[0] + "/"
                for name in names
                if "/" in name
            }
        )
    )
    for prefix in candidates:
        if _package_has_required_paths(names, prefix):
            return prefix
    raise UpdateError("更新包结构不符合 BazaarHelper 发布包。")


def _package_has_required_paths(names: set[str], prefix: str) -> bool:
    for required in REQUIRED_PACKAGE_PATHS:
        full = prefix + required
        if required in ("_internal", "data"):
            if not any(name == full or name.startswith(full + "/") for name in names):
                return False
        elif full not in names:
            return False
    return True


def _read_package_version(package: zipfile.ZipFile, payload_root: str) -> str:
    version_json = payload_root + "version.json"
    version_text = payload_root + "VERSION"
    try:
        payload = json.loads(package.read(version_json).decode("utf-8-sig"))
        version = str(payload.get("version", "")).strip()
        if version:
            parse_version(version)
            return version
    except (KeyError, ValueError, UnicodeDecodeError, TypeError):
        pass

    try:
        version = package.read(version_text).decode("utf-8-sig").strip()
    except (KeyError, UnicodeDecodeError) as exc:
        raise UpdateError("更新包缺少有效版本信息。") from exc
    parse_version(version)
    return version


def validate_update_package(
    package_path: Path,
    expected: UpdateInfo | None = None,
) -> UpdatePackageInfo:
    path = package_path.expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise UpdateError("更新包文件不存在。")
    if not zipfile.is_zipfile(path):
        raise UpdateError("更新包不是有效 zip 文件。")

    actual_sha256 = sha256_file(path)
    if expected and expected.sha256 and actual_sha256.lower() != expected.sha256:
        raise UpdateError("更新包 SHA256 与远程配置不一致。")

    with zipfile.ZipFile(path) as package:
        names = {_safe_zip_name(item.filename) for item in package.infolist() if item.filename}
        payload_root = _find_payload_root(names)
        package_version = _read_package_version(package, payload_root)

    current_version = get_current_version()
    if compare_versions(package_version, current_version) <= 0:
        raise UpdateError(
            f"更新包版本 {package_version} 不高于当前版本 {current_version}。"
        )
    if expected and compare_versions(package_version, expected.version) != 0:
        raise UpdateError(
            f"更新包版本 {package_version} 与远程配置版本 {expected.version} 不一致。"
        )

    return UpdatePackageInfo(
        path=path,
        version=package_version,
        payload_root=payload_root.rstrip("/"),
        sha256=actual_sha256,
    )


def expected_update_info() -> UpdateInfo | None:
    status = get_update_status()
    update = status.get("update")
    if isinstance(update, dict):
        return normalize_manifest(update)
    return None


def ensure_staged_package_path(path_text: str) -> Path:
    root = update_root().resolve()
    path = Path(path_text).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise UpdateError("只能安装由程序暂存的更新包。") from exc
    return path


def launch_update_install(package_path: Path, expected: UpdateInfo | None = None) -> None:
    package_info = validate_update_package(package_path, expected)
    app_root = get_app_root()
    helper = app_root / UPDATE_HELPER_NAME
    if not helper.exists():
        raise UpdateError(f"找不到更新器脚本：{helper}")

    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise UpdateError("找不到 PowerShell，无法启动更新器。")

    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper),
        "-PackagePath",
        str(package_info.path),
        "-ExpectedVersion",
        package_info.version,
        "-ExpectedSha256",
        package_info.sha256,
        "-Quiet",
        "-Relaunch",
    ]
    subprocess.Popen(command, cwd=str(app_root), close_fds=True)
