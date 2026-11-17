"""Debug engine: draws tile borders and z/x/y labels.

Lets us test the whole pipeline (selection -> job -> files -> ZIP) before the
real renderer is chosen in spike M0.5.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .base import RenderRequest


class DebugEngine:
    name = "debug"

    def __init__(self) -> None:
        self._px = 256

    def open(self, req: RenderRequest) -> None:
        self._px = req.tile_px

    def render_image(self, z: int, x: int, y: int) -> Image.Image:
        px = self._px
        hue = (x * 37 + y * 91 + z * 13) % 255
        img = Image.new("RGB", (px, px), (235, 230 - hue // 6, 210 + hue // 8))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, px - 1, px - 1], outline=(200, 40, 40))
        d.text((6, 6), f"{z}/{x}/{y}", fill=(20, 20, 20))
        return img

    def close(self) -> None:
        pass
