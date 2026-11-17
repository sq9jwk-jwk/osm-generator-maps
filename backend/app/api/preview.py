"""Preview: vector tiles from a map's MBTiles and styles rewritten to use them."""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..core.config import get_settings
from ..tiles.xyz import xyz_to_tms_y

router = APIRouter(prefix="/api", tags=["preview"])


def _mbtiles(map_id: str) -> Path:
    if not map_id.isalnum():
        raise HTTPException(400, "Zły identyfikator")
    path = get_settings().maps_dir / map_id / "vector.mbtiles"
    if not path.exists():
        raise HTTPException(404, "Mapa nie ma jeszcze kafelków wektorowych")
    return path


@lru_cache(maxsize=32)
def _metadata(path: str, mtime: float) -> dict[str, str]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return dict(conn.execute("SELECT name, value FROM metadata").fetchall())


@router.get("/maps/{map_id}/tiles/{z}/{x}/{y}.pbf")
def vector_tile(map_id: str, z: int, x: int, y: int) -> Response:
    path = _mbtiles(map_id)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, xyz_to_tms_y(y, z)),
        ).fetchone()
    if row is None:
        return Response(status_code=204)
    data: bytes = row[0]
    headers = {"Cache-Control": "public, max-age=3600"}
    if data[:2] == b"\x1f\x8b":
        headers["Content-Encoding"] = "gzip"
    return Response(data, media_type="application/x-protobuf", headers=headers)


@router.get("/styles")
def list_styles() -> list[dict[str, str]]:
    styles_dir = get_settings().styles_dir
    out = []
    for d in sorted(p for p in styles_dir.iterdir() if (p / "style.json").exists()):
        try:
            name = json.loads((d / "style.json").read_text(encoding="utf-8")).get("name", d.name)
        except json.JSONDecodeError:
            continue
        out.append({"id": d.name, "name": name})
    return out


def build_style(style_id: str, map_id: str, base_url: str) -> dict[str, Any]:
    """Load a style and point every vector source at the map's local tiles."""
    settings = get_settings()
    sdir = settings.styles_dir / style_id
    if not style_id.replace("-", "").replace("_", "").isalnum() or not (sdir / "style.json").exists():
        raise HTTPException(404, "Nieznany styl")
    style = json.loads((sdir / "style.json").read_text(encoding="utf-8"))
    path = _mbtiles(map_id)
    meta = _metadata(str(path), path.stat().st_mtime)
    maxzoom = int(meta.get("maxzoom", 14))
    minzoom = int(meta.get("minzoom", 0))
    base = base_url.rstrip("/")
    for src in style.get("sources", {}).values():
        if src.get("type") == "vector":
            src.pop("url", None)
            src["tiles"] = [f"{base}/api/maps/{map_id}/tiles/{{z}}/{{x}}/{{y}}.pbf"]
            src["minzoom"], src["maxzoom"] = minzoom, maxzoom
            src["attribution"] = "© OpenStreetMap contributors"
    if (settings.styles_dir / "fonts").is_dir():
        style["glyphs"] = f"{base}/styles/fonts/{{fontstack}}/{{range}}.pbf"
    else:
        style.pop("glyphs", None)
    if (sdir / "sprite.json").exists():
        style["sprite"] = f"{base}/styles/{style_id}/sprite"
    else:
        style.pop("sprite", None)
    return style


@router.get("/maps/{map_id}/style/{style_id}.json")
def map_style(map_id: str, style_id: str, request: Request) -> dict[str, Any]:
    return build_style(style_id, map_id, str(request.base_url))
