"""MapLibre Native engine (variant A). TO BE IMPLEMENTED IN SPIKE M0.5.

Plan:
- candidates: `mlnative` (Python, Linux x64/arm64), node `@maplibre/maplibre-gl-native`
  run as a long-lived helper process, or `mbgl-render` built from source;
- headless: xvfb-run or EGL/OSMesa;
- render a metatile (e.g. 2x2/4x4 tiles) at pixel_ratio = tile_px / 256 and cut
  it into tiles so labels are not clipped at tile edges;
- the style is served with sources rewritten to local mbtiles (see
  api/preview.py: build_style), glyphs and sprites from styles/.
"""

from __future__ import annotations

from PIL import Image

from .base import RenderRequest


class MapLibreNativeEngine:
    name = "maplibre"

    def open(self, req: RenderRequest) -> None:
        raise NotImplementedError(
            "MapLibre Native engine is not implemented yet (spike M0.5). "
            "Set [render] engine = \"debug\" in config.toml for now."
        )

    def render_image(self, z: int, x: int, y: int) -> Image.Image:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass
