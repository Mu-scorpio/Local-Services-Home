from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import services as svc
from backend.config import (
    FRONTEND_DIR,
    MANAGER_HOST,
    MANAGER_PORT,
    ensure_data_dirs,
    get_storage_settings,
    get_theme,
    set_storage_directory,
    set_theme,
)
from backend.folder_picker import FolderPickCancelled, FolderPickError, pick_folder
from backend.process_info import ProcessInfoError, list_listening_ports
from backend.process_runner import ScriptError
from backend.scanner import scan_directory
from backend.services import (
    DiscoverRequest,
    ScanRequest,
    ServiceCreate,
    ServiceUpdate,
    StopRequest,
)

ensure_data_dirs()

app = FastAPI(title="Local Services Home", version="1.0.0")


class BrowseFolderRequest(BaseModel):
    initial_dir: str | None = None


class StorageSettingsUpdate(BaseModel):
    path: str | None = None


class ThemeSettingsUpdate(BaseModel):
    theme: str


@app.get("/api/health")
def health():
    return {"ok": True, "host": MANAGER_HOST, "port": MANAGER_PORT}


@app.post("/api/browse-folder")
def api_browse_folder(body: BrowseFolderRequest | None = None):
    """Open native OS folder picker (blocks until user selects or cancels)."""
    initial = body.initial_dir if body else None
    try:
        path = pick_folder(initial)
        return {"ok": True, "path": path}
    except FolderPickCancelled:
        return {"ok": False, "cancelled": True, "path": None}
    except FolderPickError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法打开文件夹选择器: {e}") from e


@app.get("/api/settings/storage")
def api_storage_settings():
    return get_storage_settings()


@app.put("/api/settings/storage")
def api_update_storage_settings(body: StorageSettingsUpdate):
    try:
        return set_storage_directory(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"保存数据目录失败：{e}") from e


@app.get("/api/settings/theme")
def api_theme_settings():
    return {"theme": get_theme()}


@app.put("/api/settings/theme")
def api_update_theme_settings(body: ThemeSettingsUpdate):
    try:
        return {"theme": set_theme(body.theme)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/services")
def api_list_services():
    return svc.list_services()


@app.get("/api/ports/listeners")
def api_port_listeners(
    protocol: str = "all",
    hide_system: bool = True,
):
    """List local listening TCP/UDP ports grouped by process (explorer view)."""
    try:
        data = list_listening_ports(protocol=protocol, hide_system=hide_system)
    except ProcessInfoError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描监听端口失败: {e}") from e

    # Annotate which ports are already registered as managed services
    registered: dict[int, dict] = {}
    try:
        for item in svc.list_services():
            port = int(item.get("port") or 0)
            if port:
                registered[port] = {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "running": bool(item.get("running")),
                }
    except Exception:
        registered = {}

    for group in data.get("groups") or []:
        for binding in group.get("bindings") or []:
            port = int(binding.get("port") or 0)
            info = registered.get(port)
            binding["managed"] = bool(info)
            binding["managed_name"] = info.get("name") if info else None
            binding["managed_id"] = info.get("id") if info else None

    data["registered_ports"] = sorted(registered.keys())
    return data


@app.get("/api/services/status")
def api_all_status():
    return svc.all_status()


@app.post("/api/services/scan")
def api_scan(body: ScanRequest):
    try:
        return scan_directory(body.directory)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/services/discover")
def api_discover(body: DiscoverRequest):
    """Discover service directory/scripts from a live listening port."""
    try:
        return svc.discover_service(body.port)
    except ProcessInfoError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/services")
def api_create(body: ServiceCreate):
    try:
        return svc.create_service(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ProcessInfoError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/services/{service_id}")
def api_get(service_id: str):
    item = svc.get_service(service_id)
    if not item:
        raise HTTPException(status_code=404, detail="服务不存在")
    return item


@app.put("/api/services/{service_id}")
def api_update(service_id: str, body: ServiceUpdate):
    try:
        return svc.update_service(service_id, body)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/services/{service_id}")
def api_delete(service_id: str):
    try:
        svc.delete_service(service_id)
        return {"ok": True}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/services/{service_id}/start")
def api_start(service_id: str):
    try:
        return svc.start_service(service_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ScriptError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/services/{service_id}/stop")
def api_stop(service_id: str, body: StopRequest | None = None):
    mode = body.mode if body else "kill"
    try:
        return svc.stop_service(service_id, mode=mode)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ScriptError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/services/{service_id}/status")
def api_status(service_id: str):
    try:
        return svc.service_status(service_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/services/{service_id}/refresh-icon")
async def api_refresh_icon(service_id: str):
    try:
        return await svc.refresh_icon(service_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/icons/{service_id}")
def api_icon(service_id: str):
    try:
        path = svc.get_icon_path(service_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="图标不存在")
    media = {
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


# Static frontend — mount after API routes
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="前端未找到")
    return FileResponse(index_path)


@app.get("/popup")
def popup():
    popup_path = FRONTEND_DIR / "popup.html"
    if not popup_path.is_file():
        raise HTTPException(status_code=404, detail="弹窗页面未找到")
    return FileResponse(popup_path)


@app.get("/{asset_path:path}")
def frontend_assets(asset_path: str):
    """Serve css/js and other frontend files."""
    # Do not hijack API
    if asset_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    target = (FRONTEND_DIR / asset_path).resolve()
    try:
        target.relative_to(FRONTEND_DIR.resolve())
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not Found") from e
    if target.is_file():
        return FileResponse(target)
    raise HTTPException(status_code=404, detail="Not Found")
