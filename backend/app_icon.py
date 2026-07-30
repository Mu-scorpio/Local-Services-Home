"""Shared application icon artwork for the tray and packaged executable."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def create_icon_image(size: int = 64) -> Image.Image:
    """Create the same home/service mark used by the frontend SVG."""
    size = max(16, int(size))
    scale = size / 128
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def box(values):
        return [round(value * scale) for value in values]

    draw.rounded_rectangle(box([6, 6, 122, 122]), radius=round(25 * scale), fill="#4285F4")
    draw.polygon(
        [(round(x * scale), round(y * scale)) for x, y in [(29, 61), (64, 33), (99, 61)]],
        fill="#ffffff",
    )
    draw.polygon(
        [(round(x * scale), round(y * scale)) for x, y in [(29, 61), (99, 61), (99, 96), (29, 96)]],
        fill="#ffffff",
    )
    draw.rectangle(box([52, 73, 76, 102]), fill="#4285F4")
    return img


def write_ico(path: str | Path) -> Path:
    """Write multi-resolution Windows icon data for PyInstaller."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image = create_icon_image(256)
    image.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target
