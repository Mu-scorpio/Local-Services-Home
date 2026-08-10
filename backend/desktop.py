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
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys._MEIPASS)
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

os.chdir(ROOT_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app_icon import create_icon_image
from backend.config import MANAGER_HOST, MANAGER_PORT, ensure_data_dirs

# ---------------------------------------------------------------------------
# Backend server
# ---------------------------------------------------------------------------

_server_started = threading.Event()


def _kill_existing_on_port():
    """Kill any existing process listening on MANAGER_PORT (stale server)."""
    import socket
    import subprocess

    try:
        with socket.create_connection((MANAGER_HOST, MANAGER_PORT), timeout=0.3):
            pass
    except OSError:
        return

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
    import uvicorn

    config = uvicorn.Config(
        "backend.main:app",
        host=MANAGER_HOST,
        port=MANAGER_PORT,
        log_level="warning",
        access_log=False,
        limit_concurrency=32,
        timeout_keep_alive=5,
    )
    server = uvicorn.Server(config)
    _server_started.set()
    server.run()


def start_backend():
    ensure_data_dirs()
    _kill_existing_on_port()
    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    import socket

    for _ in range(80):
        try:
            with socket.create_connection((MANAGER_HOST, MANAGER_PORT), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------


def _create_tray_icon_image():
    return create_icon_image(64)


def _work_area_physical_near_cursor():
    """Physical-pixel work area (left, top, right, bottom) under the cursor."""
    if sys.platform != "win32":
        return 0, 0, 1920, 1080

    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", ctypes.c_ulong),
        ]

    user32 = ctypes.windll.user32
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        pt.x, pt.y = 200, 200

    monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return int(r.left), int(r.top), int(r.right), int(r.bottom)

    rect = RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _run_on_ui(native, fn) -> bool:
    """Run fn on the WinForms UI thread. Returns True if scheduled/ran."""
    if native is None:
        return False
    try:
        from System import Action

        if getattr(native, "InvokeRequired", False):
            native.Invoke(Action(fn))
        else:
            fn()
        return True
    except Exception:
        try:
            fn()
            return True
        except Exception:
            return False


class DesktopApp:
    """Tray + frameless main window + compact popup.

    Important (Windows / EdgeChromium / pywebview):
    - The *first* create_window() is the master and owns the message loop.
      It must be a real UI window (we use main), not a 1x1 anchor.
    - pywebview.move() multiplies coords by DPI scale; Win32 APIs return
      physical pixels. Positioning the popup via native.Location avoids the
      double-scale off-screen bug on 125%/150% DPI.
    - show/hide/move from the tray thread must go through WinForms Invoke.
    """

    _POPUP_W = 380
    _POPUP_H = 520

    def __init__(self):
        self.main_window = None
        self.popup_window = None
        self.tray_icon = None
        self._popup_visible = False
        self._main_visible = False
        self._ready = threading.Event()
        self._api = self.Api(self)

    class Api:
        def __init__(self, app: "DesktopApp"):
            self._app = app

        def open_main_window(self):
            self._app.show_main_window()
            return True

        def close_popup(self):
            self._app.hide_popup()
            return True

        def close_main_window(self):
            self._app.hide_main_window()
            return True

        def minimize_main_window(self):
            return self._app.minimize_main_window()

        def toggle_main_maximize(self):
            return self._app.toggle_main_maximize()

        def open_external(self, url: str):
            import webbrowser

            webbrowser.open(url)
            return True

    # ------ Popup placement / show (native path) ------

    def _popup_native(self):
        win = self.popup_window
        return getattr(win, "native", None) if win else None

    def _main_native(self):
        win = self.main_window
        return getattr(win, "native", None) if win else None

    def _place_and_show_popup_native(self) -> bool:
        """Position + show popup using WinForms native handle (physical px)."""
        native = self._popup_native()
        if native is None:
            return False

        left, top, right, bottom = _work_area_physical_near_cursor()
        margin = 14

        def _do():
            from System.Drawing import Point
            from System.Windows.Forms import FormStartPosition, FormWindowState

            w = int(getattr(native, "Width", 0) or self._POPUP_W)
            h = int(getattr(native, "Height", 0) or self._POPUP_H)
            if w < 200 or h < 200:
                w, h = self._POPUP_W, self._POPUP_H
                try:
                    from System.Drawing import Size

                    native.Size = Size(w, h)
                except Exception:
                    pass

            x = right - w - margin
            y = bottom - h - margin
            x = max(left + margin, min(x, right - w - margin))
            y = max(top + margin, min(y, bottom - h - margin))

            native.StartPosition = FormStartPosition.Manual
            native.Location = Point(int(x), int(y))
            native.TopMost = True
            try:
                native.Opacity = 1.0
            except Exception:
                pass
            if native.WindowState == FormWindowState.Minimized:
                native.WindowState = FormWindowState.Normal
            native.Show()
            native.Activate()
            native.BringToFront()

        return _run_on_ui(native, _do)

    def _hide_popup_native(self) -> bool:
        native = self._popup_native()
        if native is None:
            return False

        def _do():
            native.Hide()

        return _run_on_ui(native, _do)

    def _show_main_native(self) -> bool:
        native = self._main_native()
        if native is None:
            return False

        def _do():
            try:
                from System.Windows.Forms import FormWindowState

                if native.WindowState == FormWindowState.Minimized:
                    native.WindowState = FormWindowState.Normal
            except Exception:
                pass
            native.Show()
            native.Activate()
            native.BringToFront()

        return _run_on_ui(native, _do)

    def _hide_main_native(self) -> bool:
        native = self._main_native()
        if native is None:
            return False

        def _do():
            native.Hide()

        return _run_on_ui(native, _do)

    def minimize_main_window(self) -> bool:
        native = self._main_native()
        if native is None:
            return False

        def _do():
            from System.Windows.Forms import FormWindowState

            native.WindowState = FormWindowState.Minimized

        return _run_on_ui(native, _do)

    def toggle_main_maximize(self) -> dict[str, bool]:
        native = self._main_native()
        if native is None:
            return {"ok": False, "maximized": False}

        state = {"maximized": False}

        def _do():
            from System.Windows.Forms import FormWindowState

            if native.WindowState == FormWindowState.Maximized:
                native.WindowState = FormWindowState.Normal
            else:
                native.WindowState = FormWindowState.Maximized
            state["maximized"] = native.WindowState == FormWindowState.Maximized

        if not _run_on_ui(native, _do):
            return {"ok": False, "maximized": False}
        return {"ok": True, **state}

    # ------ Public window API ------

    def show_main_window(self):
        if not self._ready.wait(timeout=15):
            return
        if self._show_main_native():
            self._main_visible = True
            return
        # Fallback to pywebview API
        if self.main_window:
            try:
                self.main_window.show()
                self._main_visible = True
            except Exception:
                pass

    def hide_main_window(self):
        if self._hide_main_native():
            self._main_visible = False
            return
        if self.main_window:
            try:
                self.main_window.hide()
            except Exception:
                pass
            self._main_visible = False

    def toggle_popup(self):
        if self._popup_visible:
            self.hide_popup()
        else:
            self.show_popup()

    def show_popup(self):
        if not self._ready.wait(timeout=15):
            return
        if self._place_and_show_popup_native():
            self._popup_visible = True
            return
        # Fallback
        if self.popup_window:
            try:
                self.popup_window.show()
                self._popup_visible = True
            except Exception:
                pass

    def hide_popup(self):
        if self._hide_popup_native():
            self._popup_visible = False
            return
        if self.popup_window:
            try:
                self.popup_window.hide()
            except Exception:
                pass
            self._popup_visible = False

    def quit_app(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        os._exit(0)

    # ------ Tray ------

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

    # ------ Entry ------

    def run(self):
        import webview

        print("[desktop] starting backend...")
        if not start_backend():
            print("[desktop] backend failed to start")
            return 1

        print(f"[desktop] backend ready http://{MANAGER_HOST}:{MANAGER_PORT}")
        print("[desktop] tray init...")

        self._setup_tray()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        time.sleep(0.25)

        # FIRST window = master (message loop). Must be a real window, not 1x1.
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
            frameless=True,
            easy_drag=True,
            shadow=True,
            hidden=True,
            background_color="#0f1218",
            js_api=self._api,
        )

        def on_main_closing():
            self.hide_main_window()
            return False

        self.main_window.events.closing += on_main_closing

        # Popup is a child window (created before start so native exists)
        popup_url = f"http://{MANAGER_HOST}:{MANAGER_PORT}/popup"
        self.popup_window = webview.create_window(
            title="服务管理",
            url=popup_url,
            width=self._POPUP_W,
            height=self._POPUP_H,
            resizable=False,
            fullscreen=False,
            on_top=True,
            frameless=True,
            easy_drag=True,
            shadow=True,
            hidden=True,
            background_color="#0f1218",
            js_api=self._api,
        )

        def on_popup_closing():
            self.hide_popup()
            return False

        self.popup_window.events.closing += on_popup_closing

        def on_shown():
            # Both frameless windows briefly Show() then Hide() when hidden=True.
            # Mark ready so tray actions and custom title bars can use native handles.
            self._ready.set()
            # Ensure they stay hidden after the bootstrap Show/Hide dance
            try:
                self._hide_popup_native()
                self._hide_main_native()
            except Exception:
                pass
            print("[desktop] ready — use tray: 快捷面板 / 打开主窗口")

        self.main_window.events.shown += on_shown

        webview.start(debug=False)
        return 0


def main():
    return DesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
