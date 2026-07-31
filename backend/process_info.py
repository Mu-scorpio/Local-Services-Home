"""Port ↔ process discovery and process-tree kill (Windows-first)."""

from __future__ import annotations

import re
import socket
import sys
import threading
import time
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

# One net_connections() scan is expensive; share it across multi-port status checks.
_LISTEN_CACHE_TTL = 1.5
_listen_cache_lock = threading.Lock()
_listen_cache_at = 0.0
_listen_cache: dict[int, list[int]] = {}


def _require_psutil() -> None:
    if psutil is None:
        raise ProcessInfoError("缺少依赖 psutil，请执行: python -m pip install psutil")


def _collect_listen_map() -> dict[int, list[int]]:
    """Build port → sorted listener PID list from a single net_connections pass."""
    _require_psutil()
    by_port: dict[int, set[int]] = {}
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
            if not c.laddr:
                continue
            port = int(c.laddr.port)
            if c.pid and c.pid > 0:
                by_port.setdefault(port, set()).add(int(c.pid))
        except (AttributeError, TypeError, ValueError):
            continue
    return {port: sorted(pids) for port, pids in by_port.items()}


def _get_listen_map(*, force: bool = False) -> dict[int, list[int]]:
    global _listen_cache_at, _listen_cache
    now = time.monotonic()
    with _listen_cache_lock:
        if not force and _listen_cache and (now - _listen_cache_at) < _LISTEN_CACHE_TTL:
            return _listen_cache
        _listen_cache = _collect_listen_map()
        _listen_cache_at = now
        return _listen_cache


def invalidate_listen_cache() -> None:
    """Drop cached listen map after start/stop so the next status is fresh."""
    global _listen_cache_at, _listen_cache
    with _listen_cache_lock:
        _listen_cache_at = 0.0
        _listen_cache = {}


def find_listener_pids(port: int, *, use_cache: bool = True) -> list[int]:
    """Return PIDs that are LISTENing on the given TCP port."""
    _require_psutil()
    if not port or port < 1 or port > 65535:
        return []
    if use_cache:
        return list(_get_listen_map().get(int(port), []))
    return list(_get_listen_map(force=True).get(int(port), []))


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
    invalidate_listen_cache()
    pids = find_listener_pids(port, use_cache=False)
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
    invalidate_listen_cache()
    pids = find_listener_pids(port, use_cache=False)
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

    invalidate_listen_cache()
    still = find_listener_pids(port, use_cache=False)
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


def _empty_runtime() -> dict[str, Any]:
    return {"running": False, "pid": None, "pids": [], "process_name": None}


def _runtime_from_pids(pids: list[int]) -> dict[str, Any]:
    if not pids:
        return _empty_runtime()
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


def port_runtime_status(port: int) -> dict[str, Any]:
    """Status payload with optional PID metadata."""
    if not port:
        return _empty_runtime()

    # Cheap TCP probe first — skip process scan when port is closed.
    from backend.port_check import check_local_port

    if not check_local_port(port):
        return _empty_runtime()

    try:
        pids = find_listener_pids(port)
    except ProcessInfoError:
        return {"running": True, "pid": None, "pids": [], "process_name": None}

    if pids:
        return _runtime_from_pids(pids)
    # Port accepts connections but listener map missed it (permissions / race).
    return {"running": True, "pid": None, "pids": [], "process_name": None}


def ports_runtime_status(ports: list[int]) -> dict[int, dict[str, Any]]:
    """
    Batch status for many ports.

    Uses one listen-map scan + per-port socket fallback only when needed,
    instead of net_connections() once per service.
    """
    unique = sorted({int(p) for p in ports if p and 1 <= int(p) <= 65535})
    result: dict[int, dict[str, Any]] = {0: _empty_runtime()}
    if not unique:
        return result

    listen_map: dict[int, list[int]] = {}
    try:
        listen_map = _get_listen_map()
    except ProcessInfoError:
        listen_map = {}

    from backend.port_check import check_local_port

    for port in unique:
        pids = list(listen_map.get(port, []))
        if pids:
            result[port] = _runtime_from_pids(pids)
            continue
        # Not in listen map — confirm with a fast local TCP probe.
        if check_local_port(port):
            result[port] = {
                "running": True,
                "pid": None,
                "pids": [],
                "process_name": None,
            }
        else:
            result[port] = _empty_runtime()
    return result


# Common Windows system / noise processes (hide by default in explorer UI).
_SYSTEM_PROCESS_NAMES = frozenset(
    {
        "system",
        "idle",
        "registry",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "svchost",
        "fontdrvhost.exe",
        "dwm.exe",
        "winlogon.exe",
        "spoolsv.exe",
        "memory compression",
        "system idle process",
        "secure system",
    }
)


def _format_listen_address(ip: str | None, port: int) -> str:
    if not ip or ip in ("0.0.0.0", "::", "::0", "*"):
        return str(port)
    # IPv6 needs brackets when combined with port
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]:{port}"
    return f"{ip}:{port}"


def _is_loopback_or_any(ip: str | None) -> bool:
    if not ip or ip in ("0.0.0.0", "::", "::0", "*"):
        return True
    if ip.startswith("127.") or ip in ("::1",):
        return True
    # link-local often used by Windows services
    if ip.startswith("169.254."):
        return True
    return False


def list_listening_ports(
    *,
    protocol: str = "all",
    hide_system: bool = True,
    local_only: bool = False,
) -> dict[str, Any]:
    """
    Snapshot of local listening sockets, grouped by process.

    Inspired by FRP-style local port explorers: process card + protocol chips.
    """
    _require_psutil()
    proto_filter = (protocol or "all").strip().lower()
    if proto_filter not in {"all", "tcp", "udp"}:
        proto_filter = "all"

    # kind=inet covers IPv4+IPv6 TCP/UDP
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        conns = []
        for proc in psutil.process_iter(["pid"]):
            try:
                for c in proc.connections(kind="inet"):
                    conns.append(c)
            except (psutil.Error, OSError):
                continue

    # (pid) -> list of binding dicts
    by_pid: dict[int, list[dict[str, Any]]] = {}
    total_raw = 0

    for c in conns:
        try:
            status = getattr(c, "status", None)
            sock_type = getattr(c, "type", None)
            # TCP must be LISTEN; UDP has no listen state — bound datagram sockets count.
            if sock_type == socket.SOCK_STREAM:
                if status != psutil.CONN_LISTEN:
                    continue
                proto = "tcp"
            elif sock_type == socket.SOCK_DGRAM:
                proto = "udp"
            elif status == psutil.CONN_LISTEN:
                proto = "tcp"
            else:
                continue

            if proto_filter != "all" and proto != proto_filter:
                continue
            if not c.laddr:
                continue
            port = int(c.laddr.port)
            ip = getattr(c.laddr, "ip", None) or ""
            if local_only and not _is_loopback_or_any(str(ip)):
                continue
            pid = int(c.pid) if c.pid and c.pid > 0 else 0
            total_raw += 1
            entry = {
                "protocol": proto,
                "port": port,
                "address": str(ip) if ip else "0.0.0.0",
                "display": _format_listen_address(str(ip) if ip else None, port),
            }
            by_pid.setdefault(pid, []).append(entry)
        except (AttributeError, TypeError, ValueError, OSError):
            continue

    groups: list[dict[str, Any]] = []
    for pid, bindings in by_pid.items():
        name = "unknown"
        exe = None
        cwd = None
        if pid > 0:
            try:
                snap = get_process_snapshot(pid)
                name = snap.get("name") or name
                exe = snap.get("exe")
                cwd = snap.get("cwd")
            except ProcessInfoError:
                try:
                    name = psutil.Process(pid).name()
                except psutil.Error:
                    name = f"pid-{pid}"

        stem = (name or "").lower()
        if not stem.endswith(".exe") and "." not in stem:
            # normalize bare names
            pass
        is_system = stem in _SYSTEM_PROCESS_NAMES or stem.replace(".exe", "") + ".exe" in _SYSTEM_PROCESS_NAMES
        if hide_system and is_system:
            continue

        # Deduplicate bindings (same proto+port+address)
        seen: set[tuple] = set()
        unique_bindings: list[dict[str, Any]] = []
        for b in bindings:
            key = (b["protocol"], b["port"], b["address"])
            if key in seen:
                continue
            seen.add(key)
            unique_bindings.append(b)
        unique_bindings.sort(key=lambda x: (x["protocol"], x["port"], x["address"]))

        folder = None
        if cwd:
            folder = cwd
        elif exe:
            try:
                folder = str(Path(exe).parent)
            except OSError:
                folder = None

        groups.append(
            {
                "process_name": name,
                "pid": pid or None,
                "exe": exe,
                "cwd": cwd,
                "folder": folder,
                "is_system": is_system,
                "bindings": unique_bindings,
                "port_count": len(unique_bindings),
            }
        )

    # Sort: non-system first, more ports first, then name
    groups.sort(
        key=lambda g: (
            1 if g.get("is_system") else 0,
            -int(g.get("port_count") or 0),
            (g.get("process_name") or "").lower(),
            g.get("pid") or 0,
        )
    )

    return {
        "ok": True,
        "protocol": proto_filter,
        "hide_system": hide_system,
        "group_count": len(groups),
        "binding_count": sum(g["port_count"] for g in groups),
        "groups": groups,
    }
