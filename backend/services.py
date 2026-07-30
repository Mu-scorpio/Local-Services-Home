import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend import config
from backend.config import ensure_data_dirs
from backend.icon_fetcher import clear_icon, fetch_and_cache_icon, icon_file_for
from backend.port_check import check_local_port
from backend.process_info import (
    ProcessInfoError,
    discover_from_port,
    kill_port_processes,
    port_runtime_status,
)
from backend.process_runner import ScriptError, run_script
from backend.scanner import scan_directory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServiceCreate(BaseModel):
    name: str | None = Field(None, max_length=120)
    notes: str = ""
    directory: str | None = None
    port: int = Field(..., ge=1, le=65535)
    webui_url: str | None = None
    start_script: str | None = None
    stop_script: str | None = None
    # When true (default), try to fill missing directory/scripts from live port
    auto_discover: bool = True

    @field_validator("name", "directory", "webui_url", "start_script", "stop_script", mode="before")
    @classmethod
    def strip_optional(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def notes_default(cls, v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, str):
            return v.strip()
        return v


class ServiceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    notes: str | None = None
    directory: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    webui_url: str | None = None
    start_script: str | None = None
    stop_script: str | None = None
    enabled: bool | None = None

    @field_validator(
        "name", "notes", "directory", "webui_url", "start_script", "stop_script", mode="before"
    )
    @classmethod
    def strip_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class ScanRequest(BaseModel):
    directory: str


class DiscoverRequest(BaseModel):
    port: int = Field(..., ge=1, le=65535)


class StartRequest(BaseModel):
    hidden: bool = False


class StopRequest(BaseModel):
    # kill = end process on port (default); script = run stop_script if configured
    mode: str = "kill"


def _load_raw() -> list[dict]:
    ensure_data_dirs()
    try:
        data = json.loads(config.SERVICES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_raw(items: list[dict]) -> None:
    ensure_data_dirs()
    config.SERVICES_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _enrich(item: dict) -> dict:
    port = int(item.get("port") or 0)
    runtime = port_runtime_status(port) if port else {
        "running": False,
        "pid": None,
        "pids": [],
        "process_name": None,
    }
    # Fallback if psutil unavailable
    if not runtime.get("running") and port and check_local_port(port):
        runtime["running"] = True

    sid = item["id"]
    has_icon = icon_file_for(sid) is not None
    return {
        **item,
        "running": bool(runtime.get("running")),
        "pid": runtime.get("pid"),
        "pids": runtime.get("pids") or [],
        "process_name": runtime.get("process_name"),
        "has_icon": has_icon,
        "icon_url": f"/api/icons/{sid}" if has_icon else None,
    }


def list_services() -> list[dict]:
    return [_enrich(s) for s in _load_raw()]


def get_service(service_id: str) -> dict | None:
    for s in _load_raw():
        if s.get("id") == service_id:
            return _enrich(s)
    return None


def _default_webui(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def discover_service(port: int) -> dict:
    return discover_from_port(port)


def create_service(payload: ServiceCreate) -> dict:
    directory = payload.directory
    start_script = payload.start_script
    stop_script = payload.stop_script
    name = payload.name
    discovery = None

    if payload.auto_discover and (not directory or not start_script):
        try:
            discovery = discover_from_port(payload.port)
            directory = directory or discovery.get("suggested_directory")
            start_script = start_script or discovery.get("suggested_start")
            stop_script = stop_script or discovery.get("suggested_stop")
            if not name:
                name = discovery.get("suggested_name")
        except ProcessInfoError:
            # Port not live — require directory for offline add
            if not directory:
                raise ValueError(
                    f"端口 {payload.port} 当前无监听进程，且未提供服务目录。"
                    "请先启动服务后点「从端口探测」，或手动选择文件夹。"
                ) from None

    if not directory:
        raise ValueError("请提供服务目录，或在服务运行时通过端口自动探测")

    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.is_dir():
        raise ValueError(f"目录不存在: {dir_path}")

    if not start_script:
        try:
            scan = scan_directory(dir_path)
            start_script = scan.get("suggested_start")
            if not stop_script:
                stop_script = scan.get("suggested_stop")
        except FileNotFoundError:
            pass

    if not name:
        name = dir_path.name

    webui = payload.webui_url or _default_webui(payload.port)

    now = _now_iso()
    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "notes": payload.notes or "",
        "directory": str(dir_path),
        "port": payload.port,
        "webui_url": webui,
        "start_script": start_script,
        "stop_script": stop_script,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    items = _load_raw()
    items.append(item)
    _save_raw(items)
    return _enrich(item)


def update_service(service_id: str, payload: ServiceUpdate) -> dict:
    items = _load_raw()
    idx = next((i for i, s in enumerate(items) if s.get("id") == service_id), None)
    if idx is None:
        raise KeyError("服务不存在")

    item = items[idx]
    data = payload.model_dump(exclude_unset=True)

    if "directory" in data and data["directory"]:
        directory = Path(data["directory"]).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"目录不存在: {directory}")
        data["directory"] = str(directory)

    if "port" in data and data["port"] and "webui_url" not in data:
        old_default = _default_webui(item["port"])
        if item.get("webui_url") in (None, "", old_default):
            data["webui_url"] = _default_webui(data["port"])

    for k, v in data.items():
        item[k] = v
    item["updated_at"] = _now_iso()
    items[idx] = item
    _save_raw(items)
    return _enrich(item)


def delete_service(service_id: str) -> None:
    items = _load_raw()
    new_items = [s for s in items if s.get("id") != service_id]
    if len(new_items) == len(items):
        raise KeyError("服务不存在")
    _save_raw(new_items)
    clear_icon(service_id)


def start_service(service_id: str, *, hidden: bool = False) -> dict:
    item = _get_raw(service_id)
    port = int(item.get("port") or 0)
    if port and check_local_port(port):
        return {
            "ok": True,
            "already_running": True,
            "message": f"端口 {port} 已在监听，服务似乎已在运行",
        }

    script = item.get("start_script")
    directory = item.get("directory")
    if not script or not directory:
        raise ScriptError("未配置启动脚本或服务目录，请先编辑服务并指定启动脚本")
    pid = run_script(directory, script, hidden=hidden)
    return {
        "ok": True,
        "pid": pid,
        "hidden": hidden,
        "message": "已发送启动命令，状态将稍后更新",
    }


def stop_service(service_id: str, *, mode: str = "kill") -> dict:
    item = _get_raw(service_id)
    port = int(item.get("port") or 0)
    if not port:
        raise ScriptError("未配置端口，无法定位进程")

    mode = (mode or "kill").lower()
    if mode == "script":
        script = item.get("stop_script")
        if not script:
            raise ScriptError("未配置停止脚本")
        pid = run_script(item["directory"], script, hidden=True)
        return {"ok": True, "pid": pid, "mode": "script", "message": "已执行停止脚本，状态将稍后更新"}

    # Default: kill listener process on port
    try:
        result = kill_port_processes(port, include_children=True)
    except ProcessInfoError as e:
        # Fallback to stop script if kill cannot find process
        script = item.get("stop_script")
        if script and item.get("directory"):
            pid = run_script(item["directory"], script, hidden=True)
            return {
                "ok": True,
                "pid": pid,
                "mode": "script_fallback",
                "message": f"{e}；已改用停止脚本",
            }
        raise ScriptError(str(e)) from e

    return {
        "ok": result.get("ok", False),
        "mode": "kill",
        "killed": result.get("killed") or [],
        "still_listening": result.get("still_listening") or [],
        "message": result.get("message") or "已尝试停止服务",
    }


def service_status(service_id: str) -> dict:
    item = _get_raw(service_id)
    port = int(item.get("port") or 0)
    runtime = port_runtime_status(port) if port else {
        "running": False,
        "pid": None,
        "pids": [],
        "process_name": None,
    }
    if not runtime.get("running") and port and check_local_port(port):
        runtime["running"] = True
    return {
        "id": service_id,
        "port": port,
        **runtime,
    }


def all_status() -> list[dict]:
    result = []
    for s in _load_raw():
        port = int(s.get("port") or 0)
        runtime = port_runtime_status(port) if port else {
            "running": False,
            "pid": None,
            "pids": [],
            "process_name": None,
        }
        if not runtime.get("running") and port and check_local_port(port):
            runtime["running"] = True
        result.append({"id": s["id"], "port": port, **runtime})
    return result


async def refresh_icon(service_id: str) -> dict:
    item = _get_raw(service_id)
    path = await fetch_and_cache_icon(
        service_id,
        item.get("webui_url") or _default_webui(item["port"]),
        port=int(item.get("port") or 0),
    )
    return {
        "ok": path is not None,
        "has_icon": path is not None,
        "icon_url": f"/api/icons/{service_id}" if path else None,
    }


def get_icon_path(service_id: str) -> Path | None:
    _get_raw(service_id)
    return icon_file_for(service_id)


def _get_raw(service_id: str) -> dict:
    for s in _load_raw():
        if s.get("id") == service_id:
            return s
    raise KeyError("服务不存在")
