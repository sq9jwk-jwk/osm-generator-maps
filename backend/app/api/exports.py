"""Exports (folders with vector tiles made from a source): info, coverage, log, delete."""

from __future__ import annotations

import json
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import db
from ..core.config import get_settings
from ..importer import exports as ex
from ..jobs import queue
from ..osm import search as search_mod

router = APIRouter(prefix="/api/exports", tags=["exports"])


def get_export_or_404(export_id: str) -> dict[str, Any]:
    if not export_id.isalnum():
        raise HTTPException(400, "Zły identyfikator")
    exp = ex.get_export(export_id)
    if exp is None:
        raise HTTPException(404, "Nie ma takiego eksportu")
    return exp


@router.get("/{export_id}")
def get_export(export_id: str) -> dict[str, Any]:
    return ex.with_usage(get_export_or_404(export_id))


@router.get("/{export_id}/coverage")
def get_coverage(export_id: str) -> dict[str, Any]:
    exp = get_export_or_404(export_id)
    path = ex.export_dir(exp["map_id"], export_id) / ex.COVERAGE
    if not path.exists():
        raise HTTPException(404, "Maska pokrycia jeszcze nie istnieje")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@router.get("/{export_id}/log")
def get_log(export_id: str) -> dict[str, str]:
    exp = get_export_or_404(export_id)
    path = ex.export_dir(exp["map_id"], export_id) / ex.LOG
    text = path.read_text(encoding="utf-8", errors="replace")[-20000:] if path.exists() else ""
    return {"log": text}


@router.get("/{export_id}/search")
def search_export(export_id: str, q: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Address / place suggestions from the export's offline index."""
    exp = get_export_or_404(export_id)
    index = ex.export_dir(exp["map_id"], export_id) / ex.SEARCH
    if not index.exists():
        raise HTTPException(409, "Ten eksport nie ma indeksu wyszukiwania")
    return search_mod.search(index, q[:100], max(1, min(limit, 25)))


@router.post("/{export_id}/search/index")
def build_search_index(export_id: str) -> dict[str, str]:
    """Queue building (or rebuilding) the search index of an export."""
    exp = get_export_or_404(export_id)
    if exp["status"] != "ready":
        raise HTTPException(409, "Eksport nie jest gotowy")
    with db.connect() as c:
        if ex.active_jobs(c, export_id=export_id):
            raise HTTPException(409, "Eksport ma aktywne zadania")
    job_id = queue.enqueue("geocode", {}, map_id=exp["map_id"], export_id=export_id)
    return {"job_id": job_id}


@router.delete("/{export_id}")
def delete_export(export_id: str, outputs: bool = False) -> dict[str, Any]:
    """Delete the export folder. Rendered results are only removed when ``outputs`` is true."""
    exp = get_export_or_404(export_id)
    settings = get_settings()
    with db.connect() as c:
        if ex.active_jobs(c, export_id=export_id):
            raise HTTPException(409, "Eksport ma aktywne zadania")
        c.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        c.execute("DELETE FROM jobs WHERE export_id = ? AND status IN ('done','failed','cancelled')", (export_id,))
    edir = ex.export_dir(exp["map_id"], export_id)
    freed = ex.dir_size(edir)
    shutil.rmtree(edir, ignore_errors=True)
    removed = 0
    if outputs:
        for o in ex.scan_outputs():
            if o.get("export_id") == export_id:
                d = settings.output_dir / str(o["job_id"])
                freed += ex.dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return {"ok": True, "freed_bytes": freed, "removed_outputs": removed}
