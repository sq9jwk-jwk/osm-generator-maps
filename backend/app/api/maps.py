"""Map library, resumable chunked upload and saved selections."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..core.config import get_settings
from ..importer import archive
from ..importer.profiles import DEFAULT_PROFILE, get_profile, list_profiles
from ..jobs import queue
from ..osm.analyze import looks_like_pbf

router = APIRouter(prefix="/api", tags=["maps"])


def _get_map(map_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
    m = db.row_to_dict(row)
    if m is None:
        raise HTTPException(404, "Nie ma takiej mapy")
    return m


# ------------------------------------------------------------------ maps

SOURCE_NAME = "source.osm.pbf"


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _map_outputs(map_id: str) -> list[str]:
    """Job ids of rendered outputs that came from this map."""
    root = get_settings().output_dir
    ids: list[str] = []
    if not root.exists():
        return ids
    for d in root.iterdir():
        meta = d / "metadata.json"
        if not meta.exists():
            continue
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("map_id") == map_id:
                ids.append(d.name)
        except (OSError, ValueError):
            continue
    return ids


def _with_usage(m: dict[str, Any]) -> dict[str, Any]:
    mdir = get_settings().maps_dir / m["id"]
    source = mdir / SOURCE_NAME
    source_bytes = source.stat().st_size if source.exists() else 0
    m["source_bytes"] = source_bytes
    m["import_bytes"] = max(_dir_size(mdir) - source_bytes, 0)
    m["outputs"] = len(_map_outputs(m["id"]))
    return m


@router.get("/maps")
def list_maps() -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM maps ORDER BY created_at DESC").fetchall()
    return [_with_usage(db.row_to_dict(r)) for r in rows]  # type: ignore[arg-type]


@router.get("/maps/{map_id}")
def get_map(map_id: str) -> dict[str, Any]:
    return _get_map(map_id)


@router.get("/maps/{map_id}/coverage")
def get_coverage(map_id: str) -> dict[str, Any]:
    _get_map(map_id)
    path = get_settings().maps_dir / map_id / "coverage.json"
    if not path.exists():
        raise HTTPException(404, "Maska pokrycia jeszcze nie istnieje")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/maps/{map_id}/log")
def get_import_log(map_id: str) -> dict[str, str]:
    _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    path = mdir / "import.new.log"  # latest attempt (kept when it failed)
    if not path.exists():
        path = mdir / "import.log"
    text = path.read_text(encoding="utf-8")[-20000:] if path.exists() else ""
    return {"log": text}


def _ensure_idle(c: Any, map_id: str) -> None:
    busy = c.execute(
        "SELECT 1 FROM jobs WHERE map_id = ? AND status IN ('queued','running')", (map_id,)
    ).fetchone()
    if busy:
        raise HTTPException(409, "Mapa ma aktywne zadania")


@router.delete("/maps/{map_id}")
def delete_map(
    map_id: str, import_only: bool = False, outputs: bool = False
) -> dict[str, Any]:
    """Delete a map.

    ``import_only``: keep the source PBF and archived imports, remove only the active
    import (vector MBTiles, coverage, log); the map goes back to status ``uploaded``.
    Otherwise the whole map directory (including the PBF) and saved selections
    are removed. ``outputs`` additionally deletes rendered tiles from this map.
    """
    _get_map(map_id)
    settings = get_settings()
    mdir = settings.maps_dir / map_id
    freed = 0
    with db.connect() as c:
        _ensure_idle(c, map_id)
        if import_only:
            if mdir.is_dir():
                for p in mdir.iterdir():
                    if p.name in (SOURCE_NAME, archive.ARCHIVE_DIR):
                        continue
                    freed += _dir_size(p) if p.is_dir() else p.stat().st_size
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
            c.execute(
                "UPDATE maps SET status = 'uploaded', bbox = NULL, message = NULL WHERE id = ?",
                (map_id,),
            )
        else:
            freed += _dir_size(mdir)
            c.execute("DELETE FROM maps WHERE id = ?", (map_id,))
            c.execute("DELETE FROM selections WHERE map_id = ?", (map_id,))
            c.execute("DELETE FROM jobs WHERE map_id = ?", (map_id,))
    if not import_only:
        shutil.rmtree(mdir, ignore_errors=True)
    removed_outputs = 0
    if outputs:
        for job_id in _map_outputs(map_id):
            freed += _dir_size(settings.output_dir / job_id)
            shutil.rmtree(settings.output_dir / job_id, ignore_errors=True)
            removed_outputs += 1
    return {"ok": True, "freed_bytes": freed, "removed_outputs": removed_outputs}


LOG_NAMES = ("import.log", "import.new.log")
PARTS = ("source", "import", "log")


def _part_paths(mdir: Path, part: str) -> list[Path]:
    """Files/dirs belonging to one deletable part of a map."""
    if not mdir.is_dir():
        return []
    if part == "source":
        return [mdir / SOURCE_NAME] if (mdir / SOURCE_NAME).exists() else []
    if part == "log":
        return [mdir / n for n in LOG_NAMES if (mdir / n).exists()]
    # "import": active vector tiles, coverage mask, tilemaker working files
    # (archived imports are separate and are only removed one by one)
    skip = (SOURCE_NAME, archive.ARCHIVE_DIR, *LOG_NAMES)
    return [p for p in mdir.iterdir() if p.name not in skip]


def _size(p: Path) -> int:
    return _dir_size(p) if p.is_dir() else p.stat().st_size


@router.get("/maps/{map_id}/contents")
def map_contents(map_id: str) -> dict[str, Any]:
    """What this map occupies on disk: its own parts and rendered outputs."""
    _get_map(map_id)
    settings = get_settings()
    mdir = settings.maps_dir / map_id
    labels = {
        "source": "Plik źródłowy .osm.pbf",
        "import": "Aktywny import (kafelki wektorowe, maska pokrycia)",
        "log": "Log importu",
    }
    parts = []
    for part in PARTS:
        paths = _part_paths(mdir, part)
        if paths:
            parts.append(
                {"key": part, "label": labels[part], "bytes": sum(_size(p) for p in paths)}
            )
    outs = []
    for job_id in _map_outputs(map_id):
        d = settings.output_dir / job_id
        try:
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta["size_bytes"] = _dir_size(d)
        outs.append(meta)
    outs.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return {"parts": parts, "outputs": outs, "imports": archive.list_archives(mdir)}


@router.delete("/maps/{map_id}/parts/{part}")
def delete_map_part(map_id: str, part: str) -> dict[str, Any]:
    """Delete one part of a map: ``source``, ``import`` or ``log``."""
    if part not in PARTS:
        raise HTTPException(400, "Nieznana część mapy")
    _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    freed = 0
    with db.connect() as c:
        _ensure_idle(c, map_id)
        if part == "import" and not (mdir / SOURCE_NAME).exists():
            raise HTTPException(409, "Bez pliku źródłowego usuń całą mapę")
        for p in _part_paths(mdir, part):
            freed += _size(p)
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        if part == "import":
            c.execute(
                "UPDATE maps SET status = 'uploaded', bbox = NULL, message = NULL WHERE id = ?",
                (map_id,),
            )
    return {"ok": True, "freed_bytes": freed}


@router.post("/maps/{map_id}/imports/{imp_id}/activate")
def activate_import(map_id: str, imp_id: str) -> dict[str, Any]:
    """Make an archived import the active one (the current one is archived, not deleted)."""
    m = _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    with db.connect() as c:
        _ensure_idle(c, map_id)
        try:
            meta = archive.activate(mdir, imp_id, m)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Nie ma takiego importu") from exc
        c.execute(
            "UPDATE maps SET status = 'ready', message = NULL, profile = ?, region = ?,"
            " bbox = COALESCE(?, bbox) WHERE id = ?",
            (
                meta.get("profile") or DEFAULT_PROFILE,
                json.dumps(meta["region"]) if meta.get("region") else None,
                json.dumps(meta["bbox"]) if meta.get("bbox") else None,
                map_id,
            ),
        )
    return {"ok": True}


@router.delete("/maps/{map_id}/imports/{imp_id}")
def delete_archived_import(map_id: str, imp_id: str) -> dict[str, Any]:
    _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    with db.connect() as c:
        _ensure_idle(c, map_id)
        try:
            path = archive.archive_path(mdir, imp_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Nie ma takiego importu") from exc
        freed = _dir_size(path)
        shutil.rmtree(path, ignore_errors=True)
    return {"ok": True, "freed_bytes": freed}


class ImportRequest(BaseModel):
    profile: str = DEFAULT_PROFILE
    # [west, south, east, north]; None = whole file
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


def _validated_bbox(bbox: list[float] | None, max_span: float | None = None) -> list[float] | None:
    if bbox is None:
        return None
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(400, "Niepoprawny prostokąt (W S E N)")
    if max_span is not None and (e - w > max_span or n - s > max_span):
        # keep the centre, shrink to the allowed span
        cx, cy = (w + e) / 2, (s + n) / 2
        h = max_span / 2
        w, e, s, n = cx - h, cx + h, cy - h, cy + h
    return [round(v, 6) for v in (w, s, e, n)]


def _check_profile(profile: str) -> None:
    try:
        get_profile(get_settings(), profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/profiles")
def profiles() -> list[dict[str, str]]:
    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in list_profiles(get_settings())
    ]


@router.post("/maps/{map_id}/import")
def import_map(map_id: str, body: ImportRequest) -> dict[str, str]:
    """(Re)import the source PBF with a profile, optionally limited to an area."""
    _get_map(map_id)
    if not (get_settings().maps_dir / map_id / SOURCE_NAME).exists():
        raise HTTPException(409, "Brak pliku źródłowego")
    _check_profile(body.profile)
    region = _validated_bbox(body.bbox)
    with db.connect() as c:
        _ensure_idle(c, map_id)
        c.execute("UPDATE maps SET status = 'uploaded', message = NULL WHERE id = ?", (map_id,))
    job_id = queue.enqueue("import", {"profile": body.profile, "region": region}, map_id=map_id)
    return {"job_id": job_id}


@router.post("/maps/{map_id}/reimport")
def reimport_map(map_id: str) -> dict[str, str]:
    """Repeat the import with the profile and area the map already has."""
    m = _get_map(map_id)
    return import_map(
        map_id, ImportRequest(profile=m.get("profile") or DEFAULT_PROFILE, bbox=m.get("region"))
    )


# Largest side of a sample area in degrees (about 11 km at the equator).
SAMPLE_MAX_SPAN = 0.1


@router.post("/maps/{map_id}/sample")
def create_sample(map_id: str, body: ImportRequest) -> dict[str, Any]:
    """Small trial import of a profile for a preview before the real import."""
    _get_map(map_id)
    if not (get_settings().maps_dir / map_id / SOURCE_NAME).exists():
        raise HTTPException(409, "Brak pliku źródłowego")
    _check_profile(body.profile)
    bbox = _validated_bbox(body.bbox, SAMPLE_MAX_SPAN)
    if bbox is None:
        raise HTTPException(400, "Próbka wymaga obszaru")
    with db.connect() as c:
        busy = c.execute(
            "SELECT 1 FROM jobs WHERE map_id = ? AND kind = 'sample'"
            " AND status IN ('queued','running')",
            (map_id,),
        ).fetchone()
        if busy:
            raise HTTPException(409, "Próbka jest już generowana")
    job_id = queue.enqueue("sample", {"profile": body.profile, "bbox": bbox}, map_id=map_id)
    return {"job_id": job_id, "bbox": bbox}


@router.get("/maps/{map_id}/sample")
def sample_info(map_id: str) -> dict[str, Any]:
    _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    meta = mdir / "sample.json"
    if not (meta.exists() and (mdir / "sample.mbtiles").exists()):
        return {"exists": False}
    return {"exists": True, **json.loads(meta.read_text(encoding="utf-8"))}


@router.delete("/maps/{map_id}/sample")
def delete_sample(map_id: str) -> dict[str, bool]:
    _get_map(map_id)
    mdir = get_settings().maps_dir / map_id
    for name in ("sample.mbtiles", "sample.json", "sample.log"):
        (mdir / name).unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------------------ uploads

class UploadInit(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


def _safe_name(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:200] or "map.osm.pbf"


@router.post("/uploads")
def init_upload(body: UploadInit) -> dict[str, Any]:
    settings = get_settings()
    if not body.filename.lower().endswith(".osm.pbf"):
        raise HTTPException(400, "Oczekiwany plik z rozszerzeniem .osm.pbf")
    if body.size > settings.max_upload_bytes:
        raise HTTPException(413, "Plik jest za duży")
    free = shutil.disk_usage(settings.data_dir).free
    if body.size * 3 > free:  # source + vector mbtiles + working space
        raise HTTPException(507, "Za mało miejsca na dysku")
    upload_id = db.new_id()
    with db.connect() as c:
        c.execute(
            "INSERT INTO uploads (id, filename, size_bytes, created_at) VALUES (?, ?, ?, ?)",
            (upload_id, _safe_name(body.filename), body.size, db.now()),
        )
    (settings.uploads_dir / upload_id).touch()
    return {"id": upload_id, "received": 0}


def _get_upload(upload_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    u = db.row_to_dict(row)
    if u is None:
        raise HTTPException(404, "Nie ma takiego uploadu")
    return u


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: str) -> dict[str, Any]:
    return _get_upload(upload_id)


@router.put("/uploads/{upload_id}")
async def upload_chunk(upload_id: str, offset: int, request: Request) -> dict[str, Any]:
    """Append a chunk. ``offset`` must equal bytes received so far (resumable)."""
    u = _get_upload(upload_id)
    if offset != u["received"]:
        raise HTTPException(409, detail={"expected_offset": u["received"]})
    path = get_settings().uploads_dir / upload_id
    written = 0
    with path.open("r+b") as f:
        f.seek(offset)
        async for chunk in request.stream():
            written += len(chunk)
            if offset + written > u["size_bytes"]:
                raise HTTPException(400, "Więcej danych niż zadeklarowany rozmiar")
            f.write(chunk)
        f.truncate(offset + written)
    with db.connect() as c:
        c.execute("UPDATE uploads SET received = ? WHERE id = ?", (offset + written, upload_id))
    return {"id": upload_id, "received": offset + written}


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str) -> dict[str, Any]:
    settings = get_settings()
    u = _get_upload(upload_id)
    if u["received"] != u["size_bytes"]:
        raise HTTPException(409, "Upload niekompletny")
    src = settings.uploads_dir / upload_id
    if not looks_like_pbf(src):
        src.unlink(missing_ok=True)
        with db.connect() as c:
            c.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        raise HTTPException(400, "To nie wygląda na plik OSM PBF")

    map_id = db.new_id()
    mdir = settings.maps_dir / map_id
    mdir.mkdir(parents=True)
    src.replace(mdir / "source.osm.pbf")
    name = u["filename"].removesuffix(".osm.pbf")
    with db.connect() as c:
        c.execute(
            "INSERT INTO maps (id, name, filename, size_bytes, status, created_at)"
            " VALUES (?, ?, ?, ?, 'uploaded', ?)",
            (map_id, name, u["filename"], u["size_bytes"], db.now()),
        )
        c.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    job_id = queue.enqueue("import", {}, map_id=map_id)
    return {"map_id": map_id, "job_id": job_id}


# ------------------------------------------------------------------ selections

class SelectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    grid_zoom: int = Field(ge=0, le=22)
    tiles: list[tuple[int, int]]


@router.get("/maps/{map_id}/selections")
def list_selections(map_id: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT * FROM selections WHERE map_id = ? ORDER BY created_at DESC", (map_id,)
        ).fetchall()
    return [db.row_to_dict(r) for r in rows]  # type: ignore[misc]


@router.post("/maps/{map_id}/selections")
def save_selection(map_id: str, body: SelectionIn) -> dict[str, str]:
    _get_map(map_id)
    sel_id = db.new_id()
    with db.connect() as c:
        c.execute(
            "INSERT INTO selections (id, map_id, name, grid_zoom, tiles, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sel_id, map_id, body.name, body.grid_zoom, json.dumps(body.tiles), db.now()),
        )
    return {"id": sel_id}


@router.delete("/selections/{sel_id}")
def delete_selection(sel_id: str) -> dict[str, bool]:
    with db.connect() as c:
        c.execute("DELETE FROM selections WHERE id = ?", (sel_id,))
    return {"ok": True}
