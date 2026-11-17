"""Render engine interface.

An engine turns (map, style, z, x, y) into an encoded raster tile. The rest of
the system (jobs, API) only talks to this interface, so a Mapnik engine
(variant B) can be added later without changes elsewhere.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

FORMATS = {"png": "png", "png8": "png", "jpg": "jpg"}
TILE_SIZES = (128, 256, 512)


@dataclass(frozen=True)
class RenderRequest:
    mbtiles: Path
    style_path: Path
    tile_px: int
    fmt: str  # png | png8 | jpg
    quality: int = 85


class RenderEngine(Protocol):
    name: str

    def open(self, req: RenderRequest) -> None: ...

    def render_image(self, z: int, x: int, y: int) -> Image.Image: ...

    def close(self) -> None: ...


def encode(img: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpg":
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    elif fmt == "png8":
        img.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
            buf, "PNG", optimize=True
        )
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def get_engine(name: str) -> RenderEngine:
    if name == "debug":
        from .debug import DebugEngine

        return DebugEngine()
    if name == "maplibre":
        from .maplibre_native import MapLibreNativeEngine

        return MapLibreNativeEngine()
    raise ValueError(f"unknown render engine: {name}")
