"""Slippy map / XYZ tile math (Web Mercator, EPSG:3857).

All functions are pure and fully covered by tests (see tests/test_xyz.py).
Tile coordinates follow the XYZ scheme (y = 0 at the north). Use
``xyz_to_tms_y`` when an output in TMS numbering is required.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator

MAX_LAT = 85.0511287798066
MAX_ZOOM = 22

Tile = tuple[int, int]  # (x, y) at an implied zoom
BBox = tuple[float, float, float, float]  # (west, south, east, north)


def _check_zoom(z: int) -> None:
    if not 0 <= z <= MAX_ZOOM:
        raise ValueError(f"zoom out of range: {z}")


def tiles_per_axis(z: int) -> int:
    _check_zoom(z)
    return 1 << z


def lonlat_to_tile(lon: float, lat: float, z: int) -> Tile:
    """Return the tile containing the point. Edges are clamped into range."""
    n = tiles_per_axis(z)
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    lon = max(-180.0, min(180.0, lon))
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    """North-west corner of tile (x, y); fractional values are allowed."""
    n = tiles_per_axis(z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def tile_bounds(z: int, x: int, y: int) -> BBox:
    west, north = tile_to_lonlat(x, y, z)
    east, south = tile_to_lonlat(x + 1, y + 1, z)
    return west, south, east, north


def bbox_to_tiles(bbox: BBox, z: int) -> Iterator[Tile]:
    """All tiles at zoom z intersecting the bbox."""
    west, south, east, north = bbox
    if west > east or south > north:
        raise ValueError(f"invalid bbox: {bbox}")
    x0, y0 = lonlat_to_tile(west, north, z)
    x1, y1 = lonlat_to_tile(east, south, z)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def bbox_tile_count(bbox: BBox, z: int) -> int:
    west, south, east, north = bbox
    x0, y0 = lonlat_to_tile(west, north, z)
    x1, y1 = lonlat_to_tile(east, south, z)
    return (x1 - x0 + 1) * (y1 - y0 + 1)


def parent(x: int, y: int, z: int, target_z: int) -> Tile:
    if target_z > z:
        raise ValueError("target zoom must be <= source zoom")
    _check_zoom(target_z)
    shift = z - target_z
    return x >> shift, y >> shift


def children(x: int, y: int, z: int, target_z: int) -> Iterator[Tile]:
    if target_z < z:
        raise ValueError("target zoom must be >= source zoom")
    _check_zoom(target_z)
    k = 1 << (target_z - z)
    for cx in range(x * k, x * k + k):
        for cy in range(y * k, y * k + k):
            yield cx, cy


def xyz_to_tms_y(y: int, z: int) -> int:
    """Flip the Y axis (XYZ <-> TMS). The operation is its own inverse."""
    return tiles_per_axis(z) - 1 - y


def expand_selection(tiles: Iterable[Tile], sel_z: int, out_z: int) -> set[Tile]:
    """Expand tiles selected on grid ``sel_z`` to output tiles at ``out_z``."""
    if out_z < sel_z:
        raise ValueError("output zoom must be >= selection grid zoom")
    result: set[Tile] = set()
    for x, y in tiles:
        result.update(children(x, y, sel_z, out_z))
    return result


def output_tile_count(n_selected: int, sel_z: int, out_z: int) -> int:
    if out_z < sel_z:
        raise ValueError("output zoom must be >= selection grid zoom")
    return n_selected * (4 ** (out_z - sel_z))


def regrid_selection(tiles: Iterable[Tile], from_z: int, to_z: int) -> set[Tile]:
    """Move a selection to another grid level.

    Finer grid: every selected tile is split into its children.
    Coarser grid: a parent is selected if any of its children was selected
    (partial coverage counts). This never loses selected area.
    """
    if to_z >= from_z:
        return expand_selection(tiles, from_z, to_z)
    return {parent(x, y, from_z, to_z) for x, y in tiles}


# Rough average bytes per tile, used only for UI estimates.
_AVG_BYTES_256 = {"png": 18_000, "png8": 8_000, "jpg": 14_000}


def estimate_bytes(n_tiles: int, tile_px: int, fmt: str) -> int:
    base = _AVG_BYTES_256.get(fmt, 18_000)
    return int(n_tiles * base * (tile_px / 256) ** 2)
