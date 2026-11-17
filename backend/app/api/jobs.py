"""Jobs: list, start render, cancel."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from .. import db
from ..core.config import get_settings
from ..jobs import queue
from ..render.base import TILE_SIZES
from ..tiles import xyz

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MAX_TILES_PER_JOB = 5_000_000


class RenderJobIn(BaseModel):
    map_id: str
    style: str
    grid_zoom: int = Field(ge=0, le=22)
    min_zoom: int = Field(ge=0, le=22)
    max_zoom: int = Field(ge=0, le=22)
    tiles: list[tuple[int, int]] = Field(min_length=1)
    tile_px: int = 256
    format: Literal["png", "png8", "jpg"] = "png"
    quality: int = Field(default=85, ge=10, le=100)
    scheme: Literal["xyz", "tms"] = "xyz"

    @model_validator(mode="after")
    def _check(self) -> RenderJobIn:
        if self.tile_px not in TILE_SIZES:
            raise ValueError(f"tile_px must be one of {TILE_SIZES}")
        if self.min_zoom > self.max_zoom:
            raise ValueError("min_zoom > max_zoom")
        if self.grid_zoom > self.max_zoom:
            raise ValueError("Siatka zaznaczania nie może być dokładniejsza niż zoom wyjściowy")
        n = 1 << self.grid_zoom
        if any(not (0 <= x < n and 0 <= y < n) for x, y in self.tiles):
            raise ValueError("Kafelek poza zakresem siatki")
        return self


def estimate_tiles(body: RenderJobIn) -> int:
    total = 0
    for z in range(body.min_zoom, body.max_zoom + 1):
        if z >= body.grid_zoom:
            total += xyz.output_tile_count(len(body.tiles), body.grid_zoom, z)
        else:
            total += len(xyz.regrid_selection(body.tiles, body.grid_zoom, z))
    return total


@router.get("")
def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, kind, map_id, status, progress, message, created_at, updated_at"
            " FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


FINISHED = ("done", "failed", "cancelled")


@router.delete("")
def clear_finished_jobs() -> dict[str, int]:
    """Remove finished jobs from the history. Files on disk are not touched."""
    with db.connect() as c:
        cur = c.execute(f"DELETE FROM jobs WHERE status IN ({','.join('?' * len(FINISHED))})", FINISHED)
    return {"removed": cur.rowcount}


@router.delete("/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    with db.connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Nie ma takiego zadania")
        if row["status"] not in FINISHED:
            raise HTTPException(409, "Zadanie jest aktywne, najpierw je anuluj")
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return {"ok": True}


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    job = db.row_to_dict(row)
    if job is None:
        raise HTTPException(404, "Nie ma takiego zadania")
    job["params"].pop("tiles", None)  # can be huge
    return job


@router.post("/render")
def start_render(body: RenderJobIn) -> dict[str, Any]:
    with db.connect() as c:
        m = c.execute("SELECT status FROM maps WHERE id = ?", (body.map_id,)).fetchone()
    if m is None:
        raise HTTPException(404, "Nie ma takiej mapy")
    if m["status"] != "ready":
        raise HTTPException(409, "Mapa nie jest jeszcze gotowa")
    if not (get_settings().styles_dir / body.style / "style.json").exists():
        raise HTTPException(400, "Nieznany styl")
    n = estimate_tiles(body)
    if n > MAX_TILES_PER_JOB:
        raise HTTPException(413, f"Za dużo kafelków ({n}). Zmniejsz obszar lub zoom.")
    job_id = queue.enqueue("render", body.model_dump(), map_id=body.map_id)
    return {"job_id": job_id, "tiles": n}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    return {"ok": queue.request_cancel(job_id)}
