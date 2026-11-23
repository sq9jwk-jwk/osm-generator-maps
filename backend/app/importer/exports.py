"""Exports: one folder per vector export made from a source .osm.pbf.

Layout::

    maps/<map_id>/source.osm.pbf
    maps/<map_id>/exports/<export_id>/{vector.mbtiles, coverage.json, import.log, meta.json}

An export is what the user browses and renders from. A source can have any number of them;
they are independent (there is no "active" one). Nothing here deletes data except the explicit
delete calls of the API. ``migrate_legacy`` converts the older layout (one active import in the
map folder plus ``imports/<id>/`` archives) by *moving* files into export folders.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .. import db
from ..core.config import get_settings

log = logging.getLogger("osm-tilegen.exports")

EXPORTS_DIR = "exports"
VECTOR = "vector.mbtiles"
COVERAGE = "coverage.json"
LOG = "import.log"
META = "meta.json"
FILES = (VECTOR, COVERAGE, LOG)


def export_dir(map_id: str, export_id: str) -> Path:
    return get_settings().maps_dir / map_id / EXPORTS_DIR / export_id


def dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def get_export(export_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        row = c.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    return db.row_to_dict(row)


def write_meta(edir: Path, exp: dict[str, Any]) -> None:
    """Human-readable description next to the files (the database row is the source of truth)."""
    keys = ("id", "map_id", "name", "profile", "modules", "region", "regions", "bbox", "created_at")
    edir.mkdir(parents=True, exist_ok=True)
    (edir / META).write_text(json.dumps({k: exp.get(k) for k in keys}, indent=2), encoding="utf-8")


def scan_outputs() -> list[dict[str, Any]]:
    """metadata.json of every rendered output."""
    root = get_settings().output_dir
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for d in root.iterdir():
        meta = d / "metadata.json"
        if not meta.exists():
            continue
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m["size_bytes"] = dir_size(d)
        out.append(m)
    return out


def with_usage(exp: dict[str, Any], outputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Adds ``bytes`` (folder size) and ``outputs`` (number of rendered results)."""
    exp["bytes"] = dir_size(export_dir(exp["map_id"], exp["id"]))
    if outputs is None:
        outputs = scan_outputs()
    exp["outputs"] = sum(1 for o in outputs if o.get("export_id") == exp["id"])
    return exp


def active_jobs(c: Any, *, export_id: str | None = None, map_id: str | None = None) -> bool:
    col, val = ("export_id", export_id) if export_id else ("map_id", map_id)
    row = c.execute(
        f"SELECT 1 FROM jobs WHERE {col} = ? AND status IN ('queued','running')", (val,)
    ).fetchone()
    return row is not None


# --------------------------------------------------------------------- migration

def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _profile_name(profile: str | None) -> str:
    from .profiles import list_profiles

    for p in list_profiles(get_settings()):
        if p.id == profile:
            return p.name
    return profile or "?"


def migrate_legacy() -> int:
    """Run ``_migrate_legacy`` unless another process (API or worker starting up) is doing it."""
    settings = get_settings()
    settings.ensure_dirs()
    lock = settings.data_dir / "migrate-exports.lock"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > 600:
            lock.unlink(missing_ok=True)  # stale lock of a crashed process
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 0
    try:
        os.close(fd)
        return _migrate_legacy()
    finally:
        lock.unlink(missing_ok=True)


def _migrate_legacy() -> int:
    """Move old active imports and ``imports/<id>/`` archives into export folders.

    Idempotent and non-destructive: files are moved, never deleted, and a source is only
    touched when it still has files in the old places. Returns the number of exports created.
    """
    settings = get_settings()
    created = 0
    with db.connect() as c:
        maps = [db.row_to_dict(r) or {} for r in c.execute("SELECT * FROM maps").fetchall()]
    for m in maps:
        mdir = settings.maps_dir / m["id"]
        if not mdir.is_dir():
            continue
        legacy: list[tuple[Path, dict[str, Any]]] = []
        if (mdir / VECTOR).exists():
            meta = _read_json(mdir / "import.json") or {
                "profile": m.get("profile"), "modules": m.get("modules"), "region": m.get("region"),
                "regions": m.get("regions"), "bbox": m.get("bbox"),
                "created_at": (mdir / VECTOR).stat().st_mtime,
            }
            legacy.append((mdir, meta))
        archive_root = mdir / "imports"
        if archive_root.is_dir():
            for d in sorted(archive_root.iterdir()):
                if (d / VECTOR).exists():
                    legacy.append((d, _read_json(d / "meta.json")))
        for src_dir, meta in legacy:
            try:
                created += _migrate_one(m, src_dir, meta)
            except Exception:  # noqa: BLE001 - one broken folder must not block startup
                log.exception("could not migrate %s", src_dir)
        # Tidy up only what is now empty; nothing with content is removed.
        if archive_root.is_dir():
            for d in archive_root.iterdir():
                if d.is_dir() and (d / "meta.json").exists() and not (d / VECTOR).exists():
                    others = [p for p in d.iterdir() if p.name != "meta.json"]
                    if not others:
                        (d / "meta.json").unlink()
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            if not any(archive_root.iterdir()):
                archive_root.rmdir()
        if legacy:
            (mdir / "import.json").unlink(missing_ok=True)
    return created


def _migrate_one(m: dict[str, Any], src_dir: Path, meta: dict[str, Any]) -> int:
    exp_id = db.new_id()
    dest = export_dir(m["id"], exp_id)
    dest.mkdir(parents=True)
    for name in FILES:
        if (src_dir / name).exists():
            shutil.move(str(src_dir / name), str(dest / name))
    profile = meta.get("profile") or "classic"
    region = meta.get("region")
    name = _profile_name(profile) + (" · wycinek" if region else " · cały plik")
    row = {
        "id": exp_id, "map_id": m["id"], "name": name, "profile": profile,
        "modules": meta.get("modules"), "region": region, "regions": meta.get("regions"),
        "bbox": meta.get("bbox") or m.get("bbox"), "created_at": meta.get("created_at") or time.time(),
    }
    with db.connect() as c:
        c.execute(
            "INSERT INTO exports (id, map_id, name, status, profile, modules, region, regions, bbox,"
            " created_at) VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)",
            (
                exp_id, m["id"], name, profile,
                json.dumps(row["modules"]) if row["modules"] is not None else None,
                json.dumps(region) if region else None,
                json.dumps(row["regions"]) if row["regions"] else None,
                json.dumps(row["bbox"]) if row["bbox"] else None,
                row["created_at"],
            ),
        )
        c.execute("UPDATE maps SET status = 'uploaded', message = NULL WHERE id = ?", (m["id"],))
    write_meta(dest, row)
    return 1
