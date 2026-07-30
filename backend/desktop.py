"""Desktop application entry: system tray + pywebview windows.

Usage:
    python -m backend.desktop
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# Ensure project root is on sys.path
if getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys._MEIPASS)
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

os.chdir(ROOT_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.config import MANAGER_HOST, MANAGER_PORT, ensure_data_dirs
from backend.app_icon import create_icon_image

# ---------------------------------------------------------------------------
# Backend server (run uvicorn in a daemon thread)
# ---------------------------------------------------------------------------

_server_started = threading.Event()


def _kill_existing_on_port():
    """Kill any existing process listening on MANAGER_PORT (stale server)."""
    import subprocess
    import socket

    # Check if port is already in use
    try:
        with socket.create_connection((MANAGER_HOST, MANAGER_PORT), timeout=0.3):
            pass  # Port is open, need to kill
    except OSError:
        return  # Port is free, nothing to kill

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=0x08000000,
            )
            needle = f":{MANAGER_PORT}"
            for line in out.splitlines():
                if "LISTENING" not in line.upper():
                    continue
                if needle not in line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[-1])
                        if pid > 0:
                            subprocess.run(
                                ["taskkill", "/PID", str(pid), "/F"],
                                capture_output=True,
                                creationflags=0x08000000,
                            )
                    except (ValueError, OSError):
                        pass
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass
        time.sleep(0.5)


def _run_server():
    """Start the FastAPI/uvicorn server in background."""
    import uvicorn

    config = uvicorn.Config(
        "backend.main:app",
        host=MANAGER_HOST,
        port=MANAGER_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    _server_started.set()
    server.run()


def start_backend():
    ensure_data_dirs()

    # Kill stale server from previous session
    _kill_existing_on_port()

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    # Wait until server is accepting connections
    import socket

    for _ in range(80):
        try:
            with socket.create_connection((MANAGER_HOST, MANAGER_PORT), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

def _create_tray_icon_image():
    """Generate the shared application icon for the system tray."""
    return create_icon_image(64)


class DesktopApp:
    """Manages tray icon, popup window, and main window.

    Architecture:
    - A hidden 'anchor' main window keeps webview.start() alive forever.
    - The popup window is shown/hidden on tray click.
    - The main window is shown/hidden on demand.
    - Quit via tray menu calls os._exit(0).
    """

    def __init__(self):
        self.main_window = None
        self.popup_window = None
        self.tray_icon = None
        self._popup_visible = False
        self._main_visible = False
        self._quit_event = threading.Event()
        self._started_event = threading.Event()

    # ------ pywebview API class (exposed to JS) ------
    class Api:
        """JS-callable API bridge for popup window."""

        def __init__(self, app: "DesktopApp"):
            self._app = app

        def open_main_window(self):
            self._app.show_main_window()
            return True

        def close_popup(self):
            self._app.hide_popup()
            return True

        def open_external(self, url: str):
            """Open URL in default browser."""
            import webbrowser
            webbrowser.open(url)
            return True

    # ------ Window management ------

    def _get_popup_position(self):
        """Calculate popup position near bottom-right (above taskbar)."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
        except Exception:
            screen_w, screen_h = 1920, 1080
        popup_w, popup_h = 380, 520
        x = screen_w - popup_w - 20
        y = screen_h - popup_h - 60
        return x, y, popup_w, popup_h

    def show_main_window(self):
        """Show the full-featured main window."""
        if self.main_window:
            self.main_window.show()
            self._main_visible = True

    def hide_main_window(self):
        """Hide main window (keep alive as anchor)."""
        if self.main_window:
            self.main_window.hide()
            self._main_visible = False

    def toggle_popup(self):
        """Toggle popup visibility (called from tray click)."""
        if self._popup_visible:
            self.hide_popup()
        else:
            self.show_popup()

    def show_popup(self):
        """Show the compact popup window."""
        if self.popup_window:
            self.popup_window.show()
            self._popup_visible = True

    def hide_popup(self):
        """Hide the popup window."""
        if self.popup_window:
            self.popup_window.hide()
            self._popup_visible = False

    def quit_app(self):
        """Quit the entire application."""
        self._quit_event.set()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        os._exit(0)

    # ------ Tray icon ------

    def _setup_tray(self):
        import pystray
        from pystray import MenuItem as Item

        image = _create_tray_icon_image()

        def on_toggle_popup(icon, item):
            threading.Thread(target=self.toggle_popup, daemon=True).start()

        def on_show_main(icon, item):
            threading.Thread(target=self.show_main_window, daemon=True).start()

        def on_quit(icon, item):
            self.quit_app()

        menu = pystray.Menu(
            Item("快捷面板", on_toggle_popup, default=True),
            Item("打开主窗口", on_show_main),
            pystray.Menu.SEPARATOR,
            Item("退出", on_quit),
        )

        self.tray_icon = pystray.Icon(
            name="LocalServiceManager",
            icon=image,
            title="本地服务管理",
            menu=menu,
        )

    # ------ Main entry ------

    def run(self):
        """Start the desktop application."""
        import webview

        print("[桌面模式] 正在启动后端服务...")
        if not start_backend():
            print("[错误] 后端启动失败")
            return 1

        print(f"[桌面模式] 后端已就绪 http://{MANAGER_HOST}:{MANAGER_PORT}")
        print("[桌面模式] 正在初始化系统托盘...")

        self._setup_tray()

        # Run tray icon in a daemon thread
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
        time.sleep(0.3)

        # --- Create windows BEFORE webview.start() ---

        # 1. Main window (hidden anchor - keeps webview event loop alive)
        main_url = f"http://{MANAGER_HOST}:{MANAGER_PORT}/"
        self.main_window = webview.create_window(
            title="本地服务管理",
            url=main_url,
            width=1100,
            height=750,
            min_size=(800, 550),
            resizable=True,
            fullscreen=False,
            on_top=False,
            hidden=True,  # Start hidden
        )

        # 2. Popup window (visible on startup, near tray)
        popup_url = f"http://{MANAGER_HOST}:{MANAGER_PORT}/popup"
        x, y, pw, ph = self._get_popup_position()
        self.popup_window = webview.create_window(
            title="服务管理",
            url=popup_url,
            width=pw,
            height=ph,
            x=x,
            y=y,
            resizable=False,
            fullscreen=False,
            on_top=True,
            frameless=True,
            js_api=self.Api(self),
        )
        self._popup_visible = True

        print("[桌面模式] 启动完成，托盘图标已就绪。")

        # --- Event handlers ---
        # Main window: intercept close -> hide instead (keep anchor alive)
        def on_main_closing():
            self.hide_main_window()
            return False  # Prevent actual close

        self.main_window.events.closing += on_main_closing

        # Popup window: intercept close -> hide instead
        def on_popup_closing():
            self.hide_popup()
            return False  # Prevent actual close

        self.popup_window.events.closing += on_popup_closing

        # --- Start webview event loop (blocks until all windows destroyed) ---
        # Since we prevent closing, this only exits on os._exit() from quit_app
        webview.start(debug=False)

        return 0


def main():
    app = DesktopApp()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
