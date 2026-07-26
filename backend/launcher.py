"""CLI helpers used by start.bat / stop.bat (avoid fragile cmd parsing)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from backend.config import MANAGER_HOST, MANAGER_PORT, PID_FILE, ROOT_DIR, ensure_data_dirs


def _port_open(host: str = MANAGER_HOST, port: int = MANAGER_PORT, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_listener_pid(port: int = MANAGER_PORT) -> int | None:
    """Find PID listening on TCP port (Windows netstat)."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None

    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if needle not in line:
            continue
        # Prefer exact :port before whitespace
        parts = line.split()
        if len(parts) < 2:
            continue
        local = parts[1] if len(parts) > 1 else ""
        if not (local.endswith(needle) or local.endswith(f"]{needle}")):
            # also match 0.0.0.0:port / 127.0.0.1:port
            if f":{port}" not in local:
                continue
            host_part, _, port_part = local.rpartition(":")
            if port_part != str(port):
                continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def _write_pid(pid: int) -> None:
    ensure_data_dirs()
    PID_FILE.write_text(str(pid), encoding="ascii")


def _read_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        return int(PID_FILE.read_text(encoding="ascii").strip())
    except (ValueError, OSError):
        return None


def _clear_pid() -> None:
    try:
        if PID_FILE.is_file():
            PID_FILE.unlink()
    except OSError:
        pass


def _deps_ok() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
        return True
    except ImportError:
        return False


def _install_deps() -> None:
    req = ROOT_DIR / "requirements.txt"
    print("[信息] 正在安装依赖...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        cwd=str(ROOT_DIR),
    )


def _kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            creationflags=0x08000000,
        )
        return r.returncode == 0
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False


def cmd_start() -> int:
    ensure_data_dirs()
    os.chdir(ROOT_DIR)

    if not _deps_ok():
        try:
            _install_deps()
        except subprocess.CalledProcessError:
            print("[错误] 依赖安装失败。")
            return 1
        if not _deps_ok():
            print("[错误] 依赖仍不可用，请手动执行: python -m pip install -r requirements.txt")
            return 1

    url = f"http://{MANAGER_HOST}:{MANAGER_PORT}/"

    if _port_open():
        print(f"[信息] 管理器已在运行，正在打开浏览器...\n       {url}")
        webbrowser.open(url)
        return 0

    print(f"[信息] 正在启动本地服务管理首页...\n       {url}")

    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — no attached console spam
        creationflags = 0x00000008 | 0x00000200

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            MANAGER_HOST,
            "--port",
            str(MANAGER_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )

    for _ in range(40):
        if _port_open():
            pid = _find_listener_pid() or proc.pid
            _write_pid(pid)
            print("[成功] 已启动。")
            webbrowser.open(url)
            return 0
        if proc.poll() is not None:
            print("[错误] 进程意外退出，请检查端口是否被占用。")
            return 1
        time.sleep(0.5)

    print("[错误] 启动超时，请检查是否被占用或依赖缺失。")
    return 1


def cmd_stop() -> int:
    ensure_data_dirs()
    killed = False

    pid = _read_pid()
    if pid:
        print(f"[信息] 正在停止 PID {pid} ...")
        if _kill_pid(pid):
            killed = True
        _clear_pid()

    listener = _find_listener_pid()
    if listener:
        print(f"[信息] 结束占用 {MANAGER_PORT} 的进程 {listener} ...")
        if _kill_pid(listener):
            killed = True
        _clear_pid()

    if killed:
        print("[成功] 管理器已停止。")
    else:
        print("[信息] 未发现正在运行的管理器。")
    return 0


def _setup_console() -> None:
    """Best-effort console encoding so Chinese messages render on Windows."""
    if sys.platform != "win32":
        return
    try:
        # UTF-8 code page reduces mojibake when terminal supports it
        subprocess.run(["chcp", "65001"], capture_output=True, shell=True)
    except OSError:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python -m backend.launcher start|stop")
        return 0
    cmd = args[0].lower()
    if cmd == "start":
        return cmd_start()
    if cmd == "stop":
        return cmd_stop()
    print(f"Unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
