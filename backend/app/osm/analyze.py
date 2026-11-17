"""Analysis of .osm.pbf files using osmium-tool."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from ..tiles.xyz import BBox, xyz_to_tms_y

PBF_MAGIC = b"OSMHeader"


def looks_like_pbf(path: Path) -> bool:
    """Cheap header check: the first blob header must contain 'OSMHeader'."""
    with path.open("rb") as f:
        head = f.read(64)
    return PBF_MAGIC in head


def read_bbox(path: Path, osmium_bin: str = "osmium") -> BBox | None:
    """BBox from the PBF header; falls back to a full scan (-e) if missing."""
    out = subprocess.run(
        [osmium_bin, "fileinfo", "-j", str(path)],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(out.stdout)
    boxes = info.get("header", {}).get("boxes") or []
    if boxes:
        w, s, e, n = boxes[0]
        return float(w), float(s), float(e), float(n)

    out = subprocess.run(
        [osmium_bin, "fileinfo", "-e", "-j", str(path)],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(out.stdout)
    bbox = info.get("data", {}).get("bbox")
    if bbox:
        w, s, e, n = bbox
        return float(w), float(s), float(e), float(n)
    return None


def coverage_from_mbtiles(mbtiles: Path, zoom: int) -> list[tuple[int, int]]:
    """Tiles (XYZ) at ``zoom`` that contain data, read from tilemaker output.

    tilemaker only writes tiles where there is data, so the tile index of the
    vector MBTiles is a fast and exact coverage mask. MBTiles stores rows in
    TMS order, so Y is flipped here.
    """
    with sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT tile_column, tile_row FROM tiles WHERE zoom_level = ?", (zoom,)
        ).fetchall()
    return sorted((int(x), xyz_to_tms_y(int(y), zoom)) for x, y in rows)
