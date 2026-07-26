import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import ICONS_DIR
from backend.port_check import check_local_port

ICON_LINK_RE = re.compile(
    r"""<link[^>]+rel=["'](?:shortcut icon|icon|apple-touch-icon)["'][^>]*>""",
    re.IGNORECASE,
)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)


def icon_file_for(service_id: str) -> Path | None:
    """Return existing cached icon path if any."""
    for ext in (".png", ".ico", ".svg", ".jpg", ".jpeg", ".webp", ".gif"):
        p = ICONS_DIR / f"{service_id}{ext}"
        if p.is_file():
            return p
    return None


def clear_icon(service_id: str) -> None:
    for p in ICONS_DIR.glob(f"{service_id}.*"):
        try:
            p.unlink()
        except OSError:
            pass


async def fetch_and_cache_icon(
    service_id: str,
    webui_url: str,
    port: int | None = None,
) -> Path | None:
    """
    Try to download favicon from the service WebUI and cache it.
    Returns cached path or None on failure.
    """
    if port and not check_local_port(port):
        return icon_file_for(service_id)

    base = webui_url.rstrip("/") + "/"
    candidates: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            # Parse HTML for icon links
            try:
                resp = await client.get(base)
                if resp.status_code < 400 and "text/html" in resp.headers.get("content-type", ""):
                    for tag in ICON_LINK_RE.findall(resp.text):
                        m = HREF_RE.search(tag)
                        if m:
                            candidates.append(urljoin(str(resp.url), m.group(1)))
            except httpx.HTTPError:
                pass

            # Common fallbacks
            candidates.extend(
                [
                    urljoin(base, "favicon.ico"),
                    urljoin(base, "favicon.png"),
                    urljoin(base, "static/favicon.ico"),
                    urljoin(base, "assets/favicon.ico"),
                ]
            )

            # Deduplicate preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for u in candidates:
                if u not in seen:
                    seen.add(u)
                    unique.append(u)

            for url in unique:
                try:
                    r = await client.get(url)
                    if r.status_code >= 400 or not r.content:
                        continue
                    ctype = r.headers.get("content-type", "").lower()
                    if "html" in ctype or "json" in ctype or "text/plain" in ctype:
                        # Sometimes servers return HTML 404 pages with 200
                        if len(r.content) > 0 and r.content[:15].lstrip().startswith(b"<"):
                            continue
                    ext = _ext_from_url_or_type(url, ctype)
                    if not ext:
                        continue
                    clear_icon(service_id)
                    out = ICONS_DIR / f"{service_id}{ext}"
                    out.write_bytes(r.content)
                    return out
                except httpx.HTTPError:
                    continue
    except Exception:
        return icon_file_for(service_id)

    return icon_file_for(service_id)


def _ext_from_url_or_type(url: str, content_type: str) -> str | None:
    path = urlparse(url).path.lower()
    for ext in (".png", ".ico", ".svg", ".jpg", ".jpeg", ".webp", ".gif"):
        if path.endswith(ext):
            return ext
    if "svg" in content_type:
        return ".svg"
    if "png" in content_type:
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    if "icon" in content_type or "x-icon" in content_type:
        return ".ico"
    # Default for unknown binary that looked like an icon
    if content_type.startswith("image/") or not content_type:
        return ".ico"
    return None
