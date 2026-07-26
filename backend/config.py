from pathlib import Path

# Project root: parent of backend/
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
ICONS_DIR = DATA_DIR / "icons"
SERVICES_FILE = DATA_DIR / "services.json"
PID_FILE = DATA_DIR / "manager.pid"
FRONTEND_DIR = ROOT_DIR / "frontend"

MANAGER_HOST = "127.0.0.1"
MANAGER_PORT = 18888

SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1"}

START_KEYWORDS = ("start", "run", "启动", "开启", "server")
STOP_KEYWORDS = ("stop", "kill", "停止", "关闭", "shutdown")

# Exact names get highest priority (stem + preferred ext order)
PREFERRED_START_NAMES = ("start.bat", "start.cmd", "start.ps1", "run.bat", "启动.bat")
PREFERRED_STOP_NAMES = ("stop.bat", "stop.cmd", "stop.ps1", "停止.bat")


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if not SERVICES_FILE.exists():
        SERVICES_FILE.write_text("[]", encoding="utf-8")
