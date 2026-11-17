import math

import pytest

from app.tiles import xyz


def test_zoom0_single_tile():
    assert xyz.lonlat_to_tile(0, 0, 0) == (0, 0)
    assert xyz.lonlat_to_tile(179.99, -84, 0) == (0, 0)


def test_known_tile_warsaw():
    # Warsaw, Palace of Culture ~ (21.0067, 52.2319) -> z12 tile 2287/1348
    assert xyz.lonlat_to_tile(21.0067, 52.2319, 12) == (2287, 1348)


def test_edges_are_clamped():
    n = xyz.tiles_per_axis(5)
    assert xyz.lonlat_to_tile(180, -90, 5) == (n - 1, n - 1)
    assert xyz.lonlat_to_tile(-180, 90, 5) == (0, 0)


def test_tile_bounds_roundtrip():
    for z, x, y in [(0, 0, 0), (10, 571, 335), (15, 18300, 10770)]:
        w, s, e, n = xyz.tile_bounds(z, x, y)
        assert w < e and s < n
        cx, cy = (w + e) / 2, (s + n) / 2
        assert xyz.lonlat_to_tile(cx, cy, z) == (x, y)


def test_world_bounds():
    w, s, e, n = xyz.tile_bounds(0, 0, 0)
    assert w == -180 and e == 180
    assert math.isclose(n, xyz.MAX_LAT, abs_tol=1e-9)
    assert math.isclose(s, -xyz.MAX_LAT, abs_tol=1e-9)


def test_bbox_to_tiles_and_count():
    bbox = (14.07, 49.0, 24.15, 54.84)  # Poland
    tiles = list(xyz.bbox_to_tiles(bbox, 8))
    assert len(tiles) == xyz.bbox_tile_count(bbox, 8)
    assert len(set(tiles)) == len(tiles)
    with pytest.raises(ValueError):
        list(xyz.bbox_to_tiles((10, 0, 5, 1), 3))


def test_parent_children():
    kids = list(xyz.children(3, 5, 4, 6))
    assert len(kids) == 16
    assert all(xyz.parent(x, y, 6, 4) == (3, 5) for x, y in kids)
    assert list(xyz.children(3, 5, 4, 4)) == [(3, 5)]
    with pytest.raises(ValueError):
        xyz.parent(1, 1, 3, 4)
    with pytest.raises(ValueError):
        list(xyz.children(1, 1, 4, 3))


def test_tms_flip_is_involution():
    for z in range(0, 8):
        for y in range(xyz.tiles_per_axis(z)):
            assert xyz.xyz_to_tms_y(xyz.xyz_to_tms_y(y, z), z) == y
    assert xyz.xyz_to_tms_y(0, 1) == 1


def test_expand_and_count():
    sel = {(0, 0), (1, 1)}
    out = xyz.expand_selection(sel, 1, 3)
    assert len(out) == xyz.output_tile_count(2, 1, 3) == 32
    with pytest.raises(ValueError):
        xyz.expand_selection(sel, 3, 1)
    with pytest.raises(ValueError):
        xyz.output_tile_count(1, 3, 1)


def test_regrid_never_loses_area():
    sel = {(5, 5)}
    finer = xyz.regrid_selection(sel, 4, 6)
    assert len(finer) == 16
    coarser = xyz.regrid_selection({(10, 11)}, 6, 4)
    assert coarser == {(2, 2)}


def test_invalid_zoom():
    with pytest.raises(ValueError):
        xyz.tiles_per_axis(23)
    with pytest.raises(ValueError):
        xyz.tiles_per_axis(-1)


def test_estimate_bytes_scales_with_size():
    a = xyz.estimate_bytes(100, 256, "png")
    assert xyz.estimate_bytes(100, 512, "png") == 4 * a
    assert xyz.estimate_bytes(100, 128, "png") == a // 4
