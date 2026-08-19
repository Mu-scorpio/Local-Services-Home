"""Native folder picker for local desktop use (Windows/macOS)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class FolderPickCancelled(Exception):
    pass


class FolderPickError(Exception):
    pass


def pick_folder(initial_dir: str | None = None) -> str:
    """
    Open a native folder browser dialog and return the selected absolute path.
    Runs in a child process so uvicorn/async threads stay safe.
    """
    init = ""
    if initial_dir:
        p = Path(initial_dir).expanduser()
        if p.is_dir():
            init = str(p.resolve())

    # In frozen (PyInstaller exe/app) mode, sys.executable is the app itself,
    # so tkinter subprocess won't work. Use platform-native pickers.
    if getattr(sys, 'frozen', False):
        if sys.platform == "win32":
            return _pick_with_powershell(init)
        if sys.platform == "darwin":
            return _pick_with_applescript(init)
        raise FolderPickError("打包模式目前仅支持 Windows/macOS")

    # Prefer tkinter (stdlib); fall back to platform-native pickers.
    try:
        return _pick_with_tk(init)
    except Exception:
        if sys.platform == "win32":
            return _pick_with_powershell(init)
        if sys.platform == "darwin":
            return _pick_with_applescript(init)
        raise


def _pick_with_tk(initial_dir: str) -> str:
    # Keep script ASCII-only for -c reliability across encodings
    script = r"""
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

init = sys.argv[1] if len(sys.argv) > 1 else ""
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except tk.TclError:
    pass
root.update()
kwargs = {"title": "Select service folder", "mustexist": True}
if init:
    kwargs["initialdir"] = init
path = filedialog.askdirectory(**kwargs)
root.destroy()
if path:
    sys.stdout.write(str(Path(path).resolve()))
"""
    args = [sys.executable, "-c", script]
    if initial_dir:
        args.append(initial_dir)

    flags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW — dialog still shows (GUI), hide console flash
        flags = 0x08000000

    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        creationflags=flags,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise FolderPickError(err or "文件夹选择失败")
    path = (proc.stdout or "").strip()
    if not path:
        raise FolderPickCancelled("已取消选择")
    return path


def _pick_with_applescript(initial_dir: str) -> str:
    """Native macOS folder picker via AppleScript (works in frozen .app too)."""
    escaped = initial_dir.replace("\\", "\\\\").replace('"', '\\"') if initial_dir else ""
    script = 'POSIX path of (choose folder with prompt "Select service folder"'
    if escaped:
        script += f' default location POSIX file "{escaped}"'
    script += ')'

    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "cancel" in err.lower() or "user canceled" in err.lower() or "User canceled" in err:
            raise FolderPickCancelled("已取消选择")
        raise FolderPickError(err or "文件夹选择失败")
    path = (proc.stdout or "").strip()
    if not path:
        raise FolderPickCancelled("已取消选择")
    return str(Path(path).resolve())


def _pick_with_powershell(initial_dir: str) -> str:
    init_ps = initial_dir.replace("'", "''") if initial_dir else ""
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select service folder'
$dialog.ShowNewFolderButton = $true
"""
    if init_ps:
        ps += f"$dialog.SelectedPath = '{init_ps}'\n"
    ps += """
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  Write-Output $dialog.SelectedPath
  exit 0
}
exit 2
"""
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy", "Bypass",
            "-Command", ps,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        creationflags=0x08000000,
    )
    if proc.returncode == 2:
        raise FolderPickCancelled("已取消选择")
    if proc.returncode != 0:
        raise FolderPickError((proc.stderr or proc.stdout or "文件夹选择失败").strip())
    path = (proc.stdout or "").strip().splitlines()
    path = path[-1].strip() if path else ""
    if not path:
        raise FolderPickCancelled("已取消选择")
    return str(Path(path).resolve())
