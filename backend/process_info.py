"""Port ↔ process discovery and process-tree kill (Windows-first)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from backend.scanner import scan_directory

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


class ProcessInfoError(Exception):
    pass


_SCRIPT_EXTS = {".bat", ".cmd", ".ps1", ".py", ".js", ".mjs", ".ts", ".exe", ".jar"}
_SYSTEM_DIR_MARKERS = (
    "windows\\system32",
    "windows\\syswow64",
    "program files\\windowsapps",
)


def _require_psutil() -> None:
    if psutil is None:
        raise ProcessInfoError("缺少依赖 psutil，请执行: python -m pip install psutil")


def find_listener_pids(port: int) -> list[int]:
    """Return PIDs that are LISTENing on the given TCP port."""
    _require_psutil()
    if not port or port < 1 or port > 65535:
        return []

    pids: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        # Fallback: iterate processes (may miss some without elevation)
        conns = []
        for proc in psutil.process_iter(["pid"]):
            try:
                for c in proc.connections(kind="inet"):
                    conns.append(c)
            except (psutil.Error, OSError):
                continue

    for c in conns:
        try:
            if c.status != psutil.CONN_LISTEN:
                continue
            if not c.laddr or int(c.laddr.port) != int(port):
                continue
            if c.pid and c.pid > 0:
                pids.add(int(c.pid))
        except (AttributeError, TypeError, ValueError):
            continue
    return sorted(pids)


def get_process_snapshot(pid: int) -> dict[str, Any]:
    """Basic process info for a PID."""
    _require_psutil()
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            name = p.name()
            try:
                exe = p.exe()
            except (psutil.Error, OSError):
                exe = None
            try:
                cwd = p.cwd()
            except (psutil.Error, OSError):
                cwd = None
            try:
                cmdline = p.cmdline()
            except (psutil.Error, OSError):
                cmdline = []
            try:
                ppid = p.ppid()
            except (psutil.Error, OSError):
                ppid = None
        return {
            "pid": pid,
            "name": name,
            "exe": exe,
            "cwd": cwd,
            "cmdline": cmdline,
            "cmdline_str": " ".join(cmdline) if cmdline else "",
            "ppid": ppid,
        }
    except psutil.Error as e:
        raise ProcessInfoError(f"无法读取进程 {pid}: {e}") from e


def _path_from_token(token: str) -> Path | None:
    t = token.strip().strip('"').strip("'")
    if not t or t.startswith("-"):
        return None
    # file:// URLs
    if t.startswith("file:"):
        t = t[5:].lstrip("/")
        if sys.platform == "win32" and t.startswith("/"):
            t = t[1:]
    try:
        p = Path(t)
        if p.exists():
            return p.resolve()
    except OSError:
        return None
    return None


def _is_boring_dir(path: Path) -> bool:
    s = str(path).lower()
    return any(m in s for m in _SYSTEM_DIR_MARKERS)


def _candidate_dirs_from_process(snap: dict[str, Any]) -> list[Path]:
    """Infer likely service root directories from a process snapshot."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | None, *, walk_up: int = 0) -> None:
        if p is None:
            return
        try:
            cur = p if p.is_dir() else p.parent
            cur = cur.resolve()
        except OSError:
            return
        for _ in range(walk_up + 1):
            key = str(cur).lower()
            if key not in seen and cur.is_dir() and not _is_boring_dir(cur):
                seen.add(key)
                candidates.append(cur)
            parent = cur.parent
            if parent == cur:
                break
            cur = parent

    if snap.get("cwd"):
        add(Path(snap["cwd"]), walk_up=2)

    if snap.get("exe"):
        add(Path(snap["exe"]), walk_up=1)

    for token in snap.get("cmdline") or []:
        path = _path_from_token(token)
        if not path:
            continue
        if path.suffix.lower() in _SCRIPT_EXTS or path.is_file():
            add(path, walk_up=3)
        elif path.is_dir():
            add(path, walk_up=2)

    # Also regex-scan full command line for Windows paths
    cmd = snap.get("cmdline_str") or ""
    for m in re.finditer(r'[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]+', cmd):
        path = _path_from_token(m.group(0))
        if path:
            add(path, walk_up=2)

    return candidates


def _score_directory(directory: Path) -> tuple[int, dict]:
    """Higher score = more likely a managed service root."""
    try:
        scan = scan_directory(directory)
    except FileNotFoundError:
        return (-1, {})

    score = 0
    if scan.get("suggested_start"):
        score += 50
    score += min(len(scan.get("start_scripts") or []), 5) * 5
    score += min(len(scan.get("stop_scripts") or []), 3) * 2
    # Prefer non-root-ish short project folders
    if len(directory.parts) >= 3:
        score += 2
    return score, scan


def discover_from_port(port: int) -> dict[str, Any]:
    """
    Given a listening port, return process info + suggested service directory/scripts.
    Service must be running (port listening).
    """
    _require_psutil()
    pids = find_listener_pids(port)
    if not pids:
        raise ProcessInfoError(
            f"端口 {port} 当前没有监听进程。请先手动启动一次该服务，或改为手动选择目录。"
        )

    # Prefer non-system processes if multiple
    snapshots: list[dict[str, Any]] = []
    for pid in pids:
        try:
            snapshots.append(get_process_snapshot(pid))
        except ProcessInfoError:
            continue
    if not snapshots:
        raise ProcessInfoError(f"端口 {port} 有监听，但无法读取进程信息（可能需要管理员权限）")

    def snap_rank(s: dict) -> int:
        name = (s.get("name") or "").lower()
        # deprioritize system shells if something else listens (rare)
        if name in {"system", "idle"}:
            return -100
        return 0

    snapshots.sort(key=snap_rank, reverse=True)
    primary = snapshots[0]

    dir_scores: list[tuple[int, Path, dict]] = []
    for snap in snapshots:
        for d in _candidate_dirs_from_process(snap):
            score, scan = _score_directory(d)
            if score < 0:
                continue
            dir_scores.append((score, d, scan))

    # Deduplicate by path keeping best score
    best_by_path: dict[str, tuple[int, Path, dict]] = {}
    for score, d, scan in dir_scores:
        key = str(d).lower()
        prev = best_by_path.get(key)
        if prev is None or score > prev[0]:
            best_by_path[key] = (score, d, scan)

    ranked = sorted(best_by_path.values(), key=lambda x: x[0], reverse=True)

    suggested_directory = None
    suggested_start = None
    suggested_stop = None
    start_scripts: list[str] = []
    stop_scripts: list[str] = []
    other_scripts: list[str] = []
    scan_info: dict = {}

    if ranked:
        top_score, top_dir, scan_info = ranked[0]
        # Only auto-pick if we found scripts, or single strong cwd
        if top_score >= 50 or (top_score >= 0 and len(ranked) == 1):
            suggested_directory = str(top_dir)
            suggested_start = scan_info.get("suggested_start")
            suggested_stop = scan_info.get("suggested_stop")
            start_scripts = scan_info.get("start_scripts") or []
            stop_scripts = scan_info.get("stop_scripts") or []
            other_scripts = scan_info.get("other_scripts") or []
        # If no start script on best, still suggest cwd of process
        if not suggested_directory and primary.get("cwd"):
            suggested_directory = str(Path(primary["cwd"]).resolve())
            try:
                scan_info = scan_directory(suggested_directory)
                suggested_start = scan_info.get("suggested_start")
                suggested_stop = scan_info.get("suggested_stop")
                start_scripts = scan_info.get("start_scripts") or []
                stop_scripts = scan_info.get("stop_scripts") or []
                other_scripts = scan_info.get("other_scripts") or []
            except FileNotFoundError:
                pass
    elif primary.get("cwd"):
        suggested_directory = str(Path(primary["cwd"]).resolve())
        try:
            scan_info = scan_directory(suggested_directory)
            suggested_start = scan_info.get("suggested_start")
            suggested_stop = scan_info.get("suggested_stop")
            start_scripts = scan_info.get("start_scripts") or []
            stop_scripts = scan_info.get("stop_scripts") or []
            other_scripts = scan_info.get("other_scripts") or []
        except FileNotFoundError:
            pass

    suggested_name = None
    if suggested_directory:
        suggested_name = Path(suggested_directory).name

    return {
        "port": port,
        "running": True,
        "pid": primary["pid"],
        "pids": pids,
        "process_name": primary.get("name"),
        "exe": primary.get("exe"),
        "cwd": primary.get("cwd"),
        "cmdline": primary.get("cmdline_str"),
        "suggested_directory": suggested_directory,
        "directory_candidates": [str(d) for _, d, _ in ranked[:8]],
        "suggested_start": suggested_start,
        "suggested_stop": suggested_stop,
        "start_scripts": start_scripts,
        "stop_scripts": stop_scripts,
        "other_scripts": other_scripts,
        "suggested_name": suggested_name,
    }


def kill_port_processes(port: int, *, include_children: bool = True) -> dict[str, Any]:
    """
    Kill processes listening on port (and optionally their child trees).
    Returns details about what was killed.
    """
    _require_psutil()
    pids = find_listener_pids(port)
    if not pids:
        raise ProcessInfoError(f"端口 {port} 没有监听进程，服务可能已停止")

    killed: list[dict[str, Any]] = []
    errors: list[str] = []

    targets: set[int] = set(pids)
    if include_children:
        for pid in list(pids):
            try:
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    targets.add(child.pid)
            except psutil.Error:
                continue

    procs: list = []
    for pid in sorted(targets, reverse=True):
        try:
            proc = psutil.Process(pid)
            killed.append({"pid": pid, "name": proc.name()})
            procs.append(proc)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as e:
            errors.append(f"PID {pid}: {e}")

    for proc in procs:
        try:
            proc.terminate()
        except psutil.Error as e:
            errors.append(f"PID {proc.pid} terminate: {e}")

    _gone, alive = psutil.wait_procs(procs, timeout=2.0) if procs else ([], [])
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error as e:
            errors.append(f"PID {proc.pid} force kill: {e}")

    still = find_listener_pids(port)
    return {
        "ok": len(still) == 0,
        "port": port,
        "killed": killed,
        "still_listening": still,
        "errors": errors,
        "message": (
            f"已结束 {len(killed)} 个进程，端口 {port} 已释放"
            if not still
            else f"已尝试结束进程，但端口 {port} 仍被占用: {still}"
        ),
    }


def port_runtime_status(port: int) -> dict[str, Any]:
    """Status payload with optional PID metadata."""
    if not port:
        return {"running": False, "pid": None, "pids": [], "process_name": None}
    try:
        pids = find_listener_pids(port)
    except ProcessInfoError:
        from backend.port_check import check_local_port

        running = check_local_port(port)
        return {"running": running, "pid": None, "pids": [], "process_name": None}

    if not pids:
        return {"running": False, "pid": None, "pids": [], "process_name": None}

    name = None
    try:
        name = get_process_snapshot(pids[0]).get("name")
    except ProcessInfoError:
        pass
    return {
        "running": True,
        "pid": pids[0],
        "pids": pids,
        "process_name": name,
    }
