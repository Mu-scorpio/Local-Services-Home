import json
import os
import shutil
import sys
import uuid
from pathlib import Path

# Project root: handle both dev mode and PyInstaller frozen mode.
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys._MEIPASS)
    _EXE_DIR = Path(sys.executable).resolve().parent
    _LEGACY_DATA_DIR = _EXE_DIR / "data"
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    _LEGACY_DATA_DIR = ROOT_DIR / "data"

APP_DATA_NAME = "Local Services Home"


def _default_data_dir() -> Path:
    """Return a writable, user-scoped data directory outside the app folder."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_DATA_NAME
        return Path.home() / "AppData" / "Local" / APP_DATA_NAME

    # macOS uses ~/Library/Application Support by convention.
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_NAME

    # Keep Linux/BSD runs portable while preserving the same per-user isolation.
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_DATA_NAME


DEFAULT_DATA_DIR = _default_data_dir()
SETTINGS_FILE = DEFAULT_DATA_DIR / "app-settings.json"
VALID_THEMES = {"light", "dark"}


def _read_settings() -> dict[str, object]:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _read_configured_data_dir() -> Path:
    configured = _read_settings().get("data_dir")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    return DEFAULT_DATA_DIR


DATA_DIR = _read_configured_data_dir()
ICONS_DIR = DATA_DIR / "icons"
SERVICES_FILE = DATA_DIR / "services.json"
PID_FILE = DATA_DIR / "manager.pid"
FRONTEND_DIR = ROOT_DIR / "frontend"

MANAGER_HOST = "127.0.0.1"
MANAGER_PORT = 18888

SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".sh", ".command", ".zsh"}

START_KEYWORDS = ("start", "run", "启动", "开启", "server")
STOP_KEYWORDS = ("stop", "kill", "停止", "关闭", "shutdown")

# Exact names get highest priority (stem + preferred ext order)
PREFERRED_START_NAMES = (
    "start.bat", "start.cmd", "start.ps1", "start.sh", "start.command", "start.zsh",
    "run.bat", "run.sh", "run.command", "run.zsh", "启动.bat", "启动.sh",
)
PREFERRED_STOP_NAMES = (
    "stop.bat", "stop.cmd", "stop.ps1", "stop.sh", "stop.command", "stop.zsh", "停止.bat", "停止.sh",
)


def _copy_or_merge_services(source: Path, target: Path) -> None:
    """Copy services without overwriting newer data already in the target."""
    if not source.is_file():
        return
    if not target.is_file():
        shutil.copy2(source, target)
        return

    try:
        source_items = json.loads(source.read_text(encoding="utf-8"))
        target_items = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(source_items, list) or not isinstance(target_items, list):
            return
    except (OSError, ValueError):
        return

    merged = {str(item.get("id")): item for item in target_items if isinstance(item, dict) and item.get("id")}
    order = [str(item.get("id")) for item in target_items if isinstance(item, dict) and item.get("id")]
    for item in source_items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        item_id = str(item["id"])
        existing = merged.get(item_id)
        if existing is None:
            order.append(item_id)
            merged[item_id] = item
        elif str(item.get("updated_at", "")) > str(existing.get("updated_at", "")):
            merged[item_id] = item

    target.write_text(
        json.dumps([merged[item_id] for item_id in order], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _copy_data(source: Path, target: Path) -> None:
    """Copy user data to a new directory while keeping the source recoverable."""
    if source.resolve() == target.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    _copy_or_merge_services(source / "services.json", target / "services.json")

    source_icons = source / "icons"
    target_icons = target / "icons"
    if source_icons.is_dir():
        target_icons.mkdir(parents=True, exist_ok=True)
        for item in source_icons.iterdir():
            if item.is_file() and not (target_icons / item.name).exists():
                shutil.copy2(item, target_icons / item.name)


def _migrate_legacy_data() -> None:
    """Make the first move out of the app folder without deleting the old copy."""
    if SETTINGS_FILE.exists() or DEFAULT_DATA_DIR.resolve() == _LEGACY_DATA_DIR.resolve():
        return
    legacy_services = _LEGACY_DATA_DIR / "services.json"
    if legacy_services.is_file():
        _copy_data(_LEGACY_DATA_DIR, DEFAULT_DATA_DIR)


def _write_settings(data_dir: Path | None = None, theme: str | None = None) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings = _read_settings()
    if data_dir is not None and data_dir.resolve() == DEFAULT_DATA_DIR.resolve():
        settings.pop("data_dir", None)
    elif data_dir is not None:
        settings["data_dir"] = str(data_dir)
    if theme is not None:
        settings["theme"] = theme

    if not settings:
        try:
            SETTINGS_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_active_data_dir(data_dir: Path) -> None:
    global DATA_DIR, ICONS_DIR, SERVICES_FILE, PID_FILE
    DATA_DIR = data_dir
    ICONS_DIR = DATA_DIR / "icons"
    SERVICES_FILE = DATA_DIR / "services.json"
    PID_FILE = DATA_DIR / "manager.pid"


def get_storage_settings() -> dict[str, object]:
    return {
        "path": str(DATA_DIR),
        "default_path": str(DEFAULT_DATA_DIR),
        "is_custom": DATA_DIR.resolve() != DEFAULT_DATA_DIR.resolve(),
    }


def get_theme() -> str | None:
    theme = _read_settings().get("theme")
    return str(theme) if theme in VALID_THEMES else None


def set_theme(theme: str) -> str:
    normalized = str(theme).strip().lower()
    if normalized not in VALID_THEMES:
        raise ValueError("主题只能是 light 或 dark")
    _write_settings(theme=normalized)
    return normalized


def set_storage_directory(path: str | None) -> dict[str, object]:
    """Switch the persistent directory after safely copying current data."""
    if path is None or not str(path).strip():
        target = DEFAULT_DATA_DIR
    else:
        candidate = Path(str(path).strip()).expanduser()
        if not candidate.is_absolute():
            raise ValueError("数据目录必须使用绝对路径")
        target = candidate.resolve()

    if target.exists() and not target.is_dir():
        raise ValueError("数据目录路径不是文件夹")
    target.mkdir(parents=True, exist_ok=True)

    probe = target / f".write-test-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="ascii")
    except OSError as e:
        raise ValueError(f"数据目录不可写：{target}") from e
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass

    current = DATA_DIR
    _copy_data(current, target)
    _write_settings(target)
    _set_active_data_dir(target)
    ensure_data_dirs()
    return get_storage_settings()


def ensure_data_dirs() -> None:
    _migrate_legacy_data()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if not SERVICES_FILE.exists():
        SERVICES_FILE.write_text("[]", encoding="utf-8")
    # A small marker prevents the legacy app-folder copy from being merged
    # again on every request while still leaving the old folder untouched.
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text("{}", encoding="utf-8")
