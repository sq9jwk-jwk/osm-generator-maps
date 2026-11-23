"""Source library (uploaded .osm.pbf files), exports made from them, resumable upload, selections."""

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
from ..importer import exports as ex
from ..importer import modules as modules_mod
from ..importer import regions as regions_mod
from ..importer.profiles import DEFAULT_PROFILE, get_profile, list_profiles
from ..jobs import queue
from ..osm.analyze import looks_like_pbf, read_bbox

router = APIRouter(prefix="/api", tags=["maps"])


def _get_map(map_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
    m = db.row_to_dict(row)
    if m is None:
        raise HTTPException(404, "Nie ma takiej mapy")
    return m


# ------------------------------------------------------------------ sources

SOURCE_NAME = "source.osm.pbf"


def _with_usage(m: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Source row plus its size, its exports (newest first) and the number of its results."""
    source = get_settings().maps_dir / m["id"] / SOURCE_NAME
    m["source_bytes"] = source.stat().st_size if source.exists() else 0
    with db.connect() as c:
        rows = c.execute(
            "SELECT * FROM exports WHERE map_id = ? ORDER BY created_at DESC", (m["id"],)
        ).fetchall()
    m["exports"] = [ex.with_usage(db.row_to_dict(r) or {}, outputs) for r in rows]
    m["import_bytes"] = sum(e["bytes"] for e in m["exports"])
    m["outputs"] = sum(1 for o in outputs if o.get("map_id") == m["id"])
    return m


@router.get("/maps")
def list_maps() -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM maps ORDER BY created_at DESC").fetchall()
    outputs = ex.scan_outputs()
    return [_with_usage(db.row_to_dict(r) or {}, outputs) for r in rows]


@router.get("/maps/{map_id}")
def get_map(map_id: str) -> dict[str, Any]:
    return _with_usage(_get_map(map_id), ex.scan_outputs())


def _ensure_idle(c: Any, map_id: str) -> None:
    if ex.active_jobs(c, map_id=map_id):
        raise HTTPException(409, "Mapa ma aktywne zadania")


@router.delete("/maps/{map_id}")
def delete_map(map_id: str, outputs: bool = False) -> dict[str, Any]:
    """Delete a source with all its exports. ``outputs`` also deletes its rendered results."""
    _get_map(map_id)
    settings = get_settings()
    mdir = settings.maps_dir / map_id
    freed = ex.dir_size(mdir)
    with db.connect() as c:
        _ensure_idle(c, map_id)
        c.execute("DELETE FROM maps WHERE id = ?", (map_id,))
        c.execute("DELETE FROM exports WHERE map_id = ?", (map_id,))
        c.execute("DELETE FROM selections WHERE map_id = ?", (map_id,))
        c.execute("DELETE FROM jobs WHERE map_id = ?", (map_id,))
    shutil.rmtree(mdir, ignore_errors=True)
    removed = 0
    if outputs:
        for o in ex.scan_outputs():
            if o.get("map_id") == map_id:
                d = settings.output_dir / str(o["job_id"])
                freed += ex.dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return {"ok": True, "freed_bytes": freed, "removed_outputs": removed}


@router.get("/maps/{map_id}/contents")
def map_contents(map_id: str) -> dict[str, Any]:
    """What a source occupies on disk: the PBF, each export folder and rendered results."""
    m = _with_usage(_get_map(map_id), ex.scan_outputs())
    outs = [o for o in ex.scan_outputs() if o.get("map_id") == map_id]
    outs.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return {
        "source_bytes": m["source_bytes"],
        "exports": m["exports"],
        "outputs": outs,
    }


@router.delete("/maps/{map_id}/parts/source")
def delete_source(map_id: str) -> dict[str, Any]:
    """Delete only the .osm.pbf. Exports and results stay usable (new exports need the file)."""
    _get_map(map_id)
    path = get_settings().maps_dir / map_id / SOURCE_NAME
    with db.connect() as c:
        _ensure_idle(c, map_id)
    freed = path.stat().st_size if path.exists() else 0
    path.unlink(missing_ok=True)
    return {"ok": True, "freed_bytes": freed}


# ------------------------------------------------------------------ new export & sample

def _validated_bbox(bbox: list[float] | None, max_span: float | None = None) -> list[float] | None:
    if bbox is None:
        return None
    if len(bbox) != 4:
        raise HTTPException(400, "Prostokąt to cztery liczby (W S E N)")
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


def _import_modules(profile: str, modules: list[str] | None) -> list[str] | None:
    """Modules only exist for the 'custom' profile; validated and put in catalogue order."""
    if profile != modules_mod.CUSTOM_PROFILE:
        return None
    try:
        return modules_mod.normalize(modules)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/modules")
def modules_catalogue() -> list[dict[str, Any]]:
    return [
        {"id": m.id, "name": m.name, "description": m.description, "default": m.default}
        for m in modules_mod.MODULES
    ]


@router.get("/profiles")
def profiles() -> list[dict[str, str]]:
    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in list_profiles(get_settings())
    ]


class ExportRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    profile: str = DEFAULT_PROFILE
    # only for profile 'custom': ids of the enabled modules (default set when omitted)
    modules: list[str] | None = None
    # rectangles [west, south, east, north]; None or empty = whole file
    bboxes: list[list[float]] | None = None


def _default_name(profile: str, modules: list[str] | None, areas: list[list[float]] | None) -> str:
    label = next((p.name for p in list_profiles(get_settings()) if p.id == profile), profile)
    if modules is not None:
        label = f"Własny ({len(modules)} elem.)"
    return f"{label} · {'wycinek' if areas else 'cały plik'}"


@router.post("/maps/{map_id}/exports")
def create_export(map_id: str, body: ExportRequest) -> dict[str, str]:
    """Start a new export of a source: a new folder, imported in the background."""
    m = _get_map(map_id)
    if not (get_settings().maps_dir / map_id / SOURCE_NAME).exists():
        raise HTTPException(409, "Brak pliku źródłowego")
    _check_profile(body.profile)
    mods = _import_modules(body.profile, body.modules)
    boxes = [_validated_bbox(b) for b in (body.bboxes or [])]
    areas = [list(b) for b in regions_mod.merge_bboxes([tuple(b) for b in boxes])] if boxes else None  # type: ignore[arg-type]
    region = list(regions_mod.outline([tuple(a) for a in areas])) if areas else None
    name = (body.name or "").strip() or _default_name(body.profile, mods, areas)
    exp_id = db.new_id()
    with db.connect() as c:
        c.execute(
            "INSERT INTO exports (id, map_id, name, status, profile, modules, region, regions, bbox,"
            " created_at) VALUES (?, ?, ?, 'importing', ?, ?, ?, ?, ?, ?)",
            (
                exp_id, map_id, name, body.profile,
                json.dumps(mods) if mods is not None else None,
                json.dumps(region) if region else None,
                json.dumps(areas) if areas else None,
                json.dumps(m["bbox"]) if m.get("bbox") else None,
                db.now(),
            ),
        )
    ex.export_dir(map_id, exp_id).mkdir(parents=True, exist_ok=True)
    job_id = queue.enqueue(
        "import",
        {"profile": body.profile, "modules": mods, "regions": areas},
        map_id=map_id,
        export_id=exp_id,
    )
    return {"export_id": exp_id, "job_id": job_id}


# Largest side of a sample area in degrees (about 11 km at the equator).
SAMPLE_MAX_SPAN = 0.1


class SampleRequest(BaseModel):
    profile: str = DEFAULT_PROFILE
    modules: list[str] | None = None
    # [west, south, east, north]
    bbox: list[float] = Field(min_length=4, max_length=4)


@router.post("/maps/{map_id}/sample")
def create_sample(map_id: str, body: SampleRequest) -> dict[str, Any]:
    """Small trial import of a profile for a preview before creating an export."""
    _get_map(map_id)
    if not (get_settings().maps_dir / map_id / SOURCE_NAME).exists():
        raise HTTPException(409, "Brak pliku źródłowego")
    _check_profile(body.profile)
    mods = _import_modules(body.profile, body.modules)
    bbox = _validated_bbox(body.bbox, SAMPLE_MAX_SPAN)
    with db.connect() as c:
        busy = c.execute(
            "SELECT 1 FROM jobs WHERE map_id = ? AND kind = 'sample'"
            " AND status IN ('queued','running')",
            (map_id,),
        ).fetchone()
        if busy:
            raise HTTPException(409, "Próbka jest już generowana")
    job_id = queue.enqueue(
        "sample", {"profile": body.profile, "modules": mods, "bbox": bbox}, map_id=map_id
    )
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
    src.replace(mdir / SOURCE_NAME)
    name = u["filename"].removesuffix(".osm.pbf")
    bbox = None
    try:  # extent from the PBF header (cheap); the UI also works without it
        if shutil.which(settings.osmium_bin):
            bbox = read_bbox(mdir / SOURCE_NAME, settings.osmium_bin)
    except Exception:  # noqa: BLE001
        bbox = None
    with db.connect() as c:
        c.execute(
            "INSERT INTO maps (id, name, filename, size_bytes, status, bbox, created_at)"
            " VALUES (?, ?, ?, ?, 'uploaded', ?, ?)",
            (map_id, name, u["filename"], u["size_bytes"], json.dumps(bbox) if bbox else None, db.now()),
        )
        c.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    # No automatic export: the user picks profile, modules and area (POST /maps/{id}/exports).
    return {"map_id": map_id}


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
