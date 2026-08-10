import subprocess
import sys
from pathlib import Path


class ScriptError(Exception):
    pass


def resolve_script(directory: str | Path, script_relative: str) -> Path:
    """Resolve script path and ensure it stays inside directory."""
    if not script_relative or not script_relative.strip():
        raise ScriptError("未指定脚本")

    root = Path(directory).expanduser().resolve()
    # Disallow absolute paths and parent traversal
    candidate = Path(script_relative)
    if candidate.is_absolute():
        raise ScriptError("脚本路径必须相对于服务目录")
    if ".." in candidate.parts:
        raise ScriptError("脚本路径不允许包含 ..")

    full = (root / candidate).resolve()
    try:
        full.relative_to(root)
    except ValueError as e:
        raise ScriptError("脚本必须位于服务目录内") from e

    if not full.is_file():
        raise ScriptError(f"脚本不存在: {full}")
    return full


def run_script(directory: str | Path, script_relative: str) -> int:
    """
    Execute a start/stop script.
    Returns subprocess PID of the launcher process.
    """
    root = Path(directory).expanduser().resolve()
    script = resolve_script(root, script_relative)
    suffix = script.suffix.lower()

    if suffix in {".bat", ".cmd"}:
        # /c runs and exits without opening a console window.
        cmd = ["cmd.exe", "/c", str(script)]
    elif suffix == ".ps1":
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script),
        ]
    else:
        raise ScriptError(f"不支持的脚本类型: {suffix}")

    kwargs: dict = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if sys.platform == "win32":
        # CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = 0x08000000
        kwargs["startupinfo"] = _hidden_startupinfo()
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    return proc.pid


def _hidden_startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si
