"""Keep earlier imports of a map instead of overwriting them.

The *active* import lives directly in the map directory (``vector.mbtiles``,
``coverage.json``, ``import.log``, ``import.json``); preview and render only ever
look there. When a new import finishes, or an archived one is activated, the
previous active import is moved to ``imports/<id>/`` together with a
``meta.json`` (profile, area, bbox, time). Nothing is deleted automatically.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .. import db

ARCHIVE_DIR = "imports"
ACTIVE_META = "import.json"
ARCHIVE_META = "meta.json"
VECTOR = "vector.mbtiles"
COVERAGE = "coverage.json"
LOG = "import.log"
# Moved together with an import (log is optional).
MOVED = (VECTOR, COVERAGE, LOG)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_active_meta(
    mdir: Path, profile: str, region: Any, bbox: Any, areas: Any = None, modules: Any = None
) -> None:
    meta = {
        "profile": profile, "modules": modules, "region": region, "regions": areas,
        "bbox": bbox, "created_at": time.time(),
    }
    (mdir / ACTIVE_META).write_text(json.dumps(meta), encoding="utf-8")


def active_meta(mdir: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    """Description of the active import; legacy imports fall back to the maps row."""
    meta = _read_json(mdir / ACTIVE_META)
    if meta:
        return meta
    vector = mdir / VECTOR
    return {
        "profile": fallback.get("profile"),
        "modules": fallback.get("modules"),
        "region": fallback.get("region"),
        "regions": fallback.get("regions") or ([fallback["region"]] if fallback.get("region") else None),
        "bbox": fallback.get("bbox"),
        "created_at": vector.stat().st_mtime if vector.exists() else time.time(),
    }


def archive_active(mdir: Path, fallback: dict[str, Any]) -> str | None:
    """Move the active import into ``imports/<id>/``; returns the id, or None if none."""
    if not (mdir / VECTOR).exists():
        return None
    meta = active_meta(mdir, fallback)
    imp_id = db.new_id()
    dest = mdir / ARCHIVE_DIR / imp_id
    dest.mkdir(parents=True)
    for name in MOVED:
        if (mdir / name).exists():
            shutil.move(str(mdir / name), str(dest / name))
    (mdir / ACTIVE_META).unlink(missing_ok=True)
    (dest / ARCHIVE_META).write_text(json.dumps(meta), encoding="utf-8")
    return imp_id


def archive_path(mdir: Path, imp_id: str) -> Path:
    if not imp_id.isalnum():
        raise ValueError("Zły identyfikator importu")
    path = mdir / ARCHIVE_DIR / imp_id
    if not (path / VECTOR).exists():
        raise FileNotFoundError(imp_id)
    return path


def activate(mdir: Path, imp_id: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Make an archived import the active one; the current one is archived first."""
    src = archive_path(mdir, imp_id)
    meta = _read_json(src / ARCHIVE_META)
    archive_active(mdir, fallback)
    for name in MOVED:
        if (src / name).exists():
            shutil.move(str(src / name), str(mdir / name))
    (mdir / ACTIVE_META).write_text(json.dumps(meta), encoding="utf-8")
    shutil.rmtree(src, ignore_errors=True)
    return meta


def list_archives(mdir: Path) -> list[dict[str, Any]]:
    root = mdir / ARCHIVE_DIR
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if not (d / VECTOR).exists():
            continue
        meta = _read_json(d / ARCHIVE_META)
        meta["id"] = d.name
        meta["size_bytes"] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append(meta)
    out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return out
