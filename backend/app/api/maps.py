"""Map library, resumable chunked upload and saved selections."""

from __future__ import annotations

import json
import re
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..core.config import get_settings
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

@router.get("/maps")
def list_maps() -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM maps ORDER BY created_at DESC").fetchall()
    return [db.row_to_dict(r) for r in rows]  # type: ignore[misc]


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
    path = get_settings().maps_dir / map_id / "import.log"
    text = path.read_text(encoding="utf-8")[-20000:] if path.exists() else ""
    return {"log": text}


@router.delete("/maps/{map_id}")
def delete_map(map_id: str) -> dict[str, bool]:
    _get_map(map_id)
    with db.connect() as c:
        busy = c.execute(
            "SELECT 1 FROM jobs WHERE map_id = ? AND status IN ('queued','running')", (map_id,)
        ).fetchone()
        if busy:
            raise HTTPException(409, "Mapa ma aktywne zadania")
        c.execute("DELETE FROM maps WHERE id = ?", (map_id,))
        c.execute("DELETE FROM selections WHERE map_id = ?", (map_id,))
    shutil.rmtree(get_settings().maps_dir / map_id, ignore_errors=True)
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
