"""Import areas: a map's import may cover several rectangles (W, S, E, N)."""

from __future__ import annotations

import json
from pathlib import Path

BBox = tuple[float, float, float, float]


def _touch(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def merge_bboxes(boxes: list[BBox]) -> list[BBox]:
    """Join rectangles that overlap or touch into their bounding box, so the rest are disjoint."""
    out = [tuple(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if _touch(out[i], out[j]):  # type: ignore[arg-type]
                    a, b = out[i], out.pop(j)
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    merged = True
                    break
            if merged:
                break
    return out  # type: ignore[return-value]


def outline(boxes: list[BBox]) -> BBox:
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def write_multipolygon(boxes: list[BBox], path: Path) -> None:
    """GeoJSON MultiPolygon for ``osmium extract --polygon`` (boxes must be disjoint)."""
    polys = [[[[w, s], [e, s], [e, n], [w, n], [w, s]]] for w, s, e, n in boxes]
    path.write_text(
        json.dumps({"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": polys}}),
        encoding="utf-8",
    )
