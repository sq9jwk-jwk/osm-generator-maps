"""Preview: vector tiles from a map's MBTiles and styles rewritten to use them."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..core.config import get_settings
from ..importer.exports import export_dir
from ..tiles.xyz import xyz_to_tms_y
from .exports import get_export_or_404

router = APIRouter(prefix="/api", tags=["preview"])


def _export_mbtiles(export_id: str) -> Path:
    exp = get_export_or_404(export_id)
    path = export_dir(exp["map_id"], export_id) / "vector.mbtiles"
    if not path.exists():
        raise HTTPException(404, "Eksport nie ma jeszcze kafelków wektorowych")
    return path


def _sample_mbtiles(map_id: str) -> Path:
    if not map_id.isalnum():
        raise HTTPException(400, "Zły identyfikator")
    path = get_settings().maps_dir / map_id / "sample.mbtiles"
    if not path.exists():
        raise HTTPException(404, "Brak próbki")
    return path


@lru_cache(maxsize=32)
def _metadata(path: str, mtime: float) -> dict[str, str]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        return dict(conn.execute("SELECT name, value FROM metadata").fetchall())


def _tile_response(path: Path, z: int, x: int, y: int, cache: str) -> Response:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, xyz_to_tms_y(y, z)),
        ).fetchone()
    if row is None:
        return Response(status_code=204)
    data: bytes = row[0]
    headers = {"Cache-Control": cache}
    if data[:2] == b"\x1f\x8b":
        headers["Content-Encoding"] = "gzip"
    return Response(data, media_type="application/x-protobuf", headers=headers)


@router.get("/exports/{export_id}/tiles/{z}/{x}/{y}.pbf")
def export_tile(export_id: str, z: int, x: int, y: int) -> Response:
    # The style puts the file's mtime in the URL (?v=), so long caching is safe.
    return _tile_response(_export_mbtiles(export_id), z, x, y, "public, max-age=3600")


@router.get("/maps/{map_id}/sample/tiles/{z}/{x}/{y}.pbf")
def sample_tile(map_id: str, z: int, x: int, y: int) -> Response:
    return _tile_response(_sample_mbtiles(map_id), z, x, y, "no-cache")


@router.get("/styles")
def list_styles() -> list[dict[str, Any]]:
    styles_dir = get_settings().styles_dir
    out = []
    for d in sorted(p for p in styles_dir.iterdir() if (p / "style.json").exists()):
        try:
            doc = json.loads((d / "style.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        # A style may need layers that only some import profiles produce.
        profiles = doc.get("metadata", {}).get("osm-tilegen", {}).get("profiles", [])
        out.append({"id": d.name, "name": doc.get("name", d.name), "profiles": profiles})
    return out


def build_style(style_id: str, mbtiles: Path, tiles_url: str, base_url: str) -> dict[str, Any]:
    """Load a style and point every vector source at the given local tiles.

    ``tiles_url`` is the tile template without host and query, e.g.
    ``/api/exports/<id>/tiles/{z}/{x}/{y}.pbf``.
    """
    settings = get_settings()
    sdir = settings.styles_dir / style_id
    if not style_id.replace("-", "").replace("_", "").isalnum() or not (sdir / "style.json").exists():
        raise HTTPException(404, "Nieznany styl")
    style = json.loads((sdir / "style.json").read_text(encoding="utf-8"))
    mtime = mbtiles.stat().st_mtime
    meta = _metadata(str(mbtiles), mtime)
    maxzoom = int(meta.get("maxzoom", 14))
    minzoom = int(meta.get("minzoom", 0))
    base = base_url.rstrip("/")
    for src in style.get("sources", {}).values():
        if src.get("type") == "vector":
            src.pop("url", None)
            # The URL carries the file's mtime, so a new export is never served from the
            # browser's cache of an earlier one.
            src["tiles"] = [f"{base}{tiles_url}?v={int(mtime)}"]
            src["minzoom"], src["maxzoom"] = minzoom, maxzoom
            src["attribution"] = "© OpenMapTiles © OpenStreetMap contributors"
    if (settings.styles_dir / "fonts").is_dir():
        style["glyphs"] = f"{base}/styles/fonts/{{fontstack}}/{{range}}.pbf"
    else:
        style.pop("glyphs", None)
    if (sdir / "sprite.json").exists():
        style["sprite"] = f"{base}/styles/{style_id}/sprite"
    else:
        style.pop("sprite", None)
    return style


@router.get("/exports/{export_id}/style/{style_id}.json")
def export_style(export_id: str, style_id: str, request: Request) -> dict[str, Any]:
    path = _export_mbtiles(export_id)
    return build_style(
        style_id, path, f"/api/exports/{export_id}/tiles/{{z}}/{{x}}/{{y}}.pbf", str(request.base_url)
    )


@router.get("/maps/{map_id}/sample/style/{style_id}.json")
def sample_style(map_id: str, style_id: str, request: Request) -> dict[str, Any]:
    path = _sample_mbtiles(map_id)
    return build_style(
        style_id, path, f"/api/maps/{map_id}/sample/tiles/{{z}}/{{x}}/{{y}}.pbf", str(request.base_url)
    )
