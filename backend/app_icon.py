"""Shared application icon artwork for the tray and packaged executable."""

from __future__ import annotations

import subprocess
import sys
import tempfile
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


def create_macos_icon_image(size: int = 1024) -> Image.Image:
    """Render the macOS app icon using the same lighter home-line mark."""
    size = max(16, int(size))
    oversample = 2
    canvas_size = size * oversample
    scale = canvas_size / 32
    line_width = max(1, round(3.2 * scale))
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [round(4 * scale), round(4 * scale), round(28 * scale), round(28 * scale)],
        radius=round(6 * scale),
        fill="#24262B",
    )

    def cubic(start, control_a, control_b, end, steps=18):
        points = []
        for index in range(steps + 1):
            t = index / steps
            inverse = 1 - t
            points.append(
                (
                    inverse**3 * start[0]
                    + 3 * inverse**2 * t * control_a[0]
                    + 3 * inverse * t**2 * control_b[0]
                    + t**3 * end[0],
                    inverse**3 * start[1]
                    + 3 * inverse**2 * t * control_a[1]
                    + 3 * inverse * t**2 * control_b[1]
                    + t**3 * end[1],
                )
            )
        return points

    def draw_path(points):
        scaled = [(round(x * scale), round(y * scale)) for x, y in points]
        draw.line(scaled, fill="#F5F5F5", width=line_width, joint="curve")
        radius = line_width / 2
        for x, y in scaled:
            draw.ellipse((round(x - radius), round(y - radius), round(x + radius), round(y + radius)), fill="#F5F5F5")

    roof = [(5.5, 15.2), (13.9, 7.8)]
    roof += cubic((13.9, 7.8), (15.1, 6.75), (16.9, 6.75), (18.1, 7.8))[1:]
    roof.append((26.5, 15.2))
    draw_path(roof)

    body = [(8.7, 14.6), (8.7, 23.2)]
    body += cubic((8.7, 23.2), (8.7, 25.15), (10.25, 26.7), (12.2, 26.7))[1:]
    body.append((19.8, 26.7))
    body += cubic((19.8, 26.7), (21.75, 26.7), (23.3, 25.15), (23.3, 23.2))[1:]
    body.append((23.3, 14.6))
    draw_path(body)

    door = [(13.5, 26.4), (13.5, 21.5)]
    door += cubic((13.5, 21.5), (13.5, 20.1), (14.6, 19), (16, 19))[1:]
    door += cubic((16, 19), (17.4, 19), (18.5, 20.1), (18.5, 21.5))[1:]
    door.append((18.5, 26.4))
    draw_path(door)

    resampling = getattr(Image, "Resampling", Image)
    return image.resize((size, size), resample=resampling.LANCZOS)


def write_ico(path: str | Path) -> Path:
    """Write multi-resolution Windows icon data for PyInstaller."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image = create_icon_image(256)
    image.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target


def write_icns(path: str | Path) -> Path:
    """Write a macOS .icns file using the system iconutil tool."""
    if sys.platform != "darwin":
        raise RuntimeError("write_icns is only available on macOS")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    sizes = {
        "icon_16x16": 16,
        "icon_16x16@2x": 32,
        "icon_32x32": 32,
        "icon_32x32@2x": 64,
        "icon_128x128": 128,
        "icon_128x128@2x": 256,
        "icon_256x256": 256,
        "icon_256x256@2x": 512,
        "icon_512x512": 512,
        "icon_512x512@2x": 1024,
    }

    with tempfile.TemporaryDirectory(prefix="lsm-iconset-") as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for name, size in sizes.items():
            image = create_macos_icon_image(size)
            image.save(iconset / f"{name}.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            check=True,
            capture_output=True,
        )
    return target
