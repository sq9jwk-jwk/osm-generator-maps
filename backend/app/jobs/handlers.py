"""Job handlers: import (.osm.pbf -> vector MBTiles) and render (-> PNG/JPG)."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from .. import db
from ..core.config import get_settings
from ..importer import archive
from ..importer.profiles import DEFAULT_PROFILE, get_profile
from ..importer.tilemaker import extract_region, run_tilemaker
from ..osm.analyze import coverage_from_mbtiles, read_bbox
from ..render.base import FORMATS, RenderRequest, encode, get_engine
from ..tiles import xyz
from . import queue

log = logging.getLogger("osm-tilegen.jobs")


def map_dir(map_id: str) -> Path:
    return get_settings().maps_dir / map_id


def _set_map(map_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE maps SET {cols} WHERE id = ?", (*fields.values(), map_id))


# --------------------------------------------------------------------- import

def _tilemaker_with_profile(
    job: dict[str, Any],
    mdir: Path,
    emit: Any,
    out: Path,
    profile_id: str,
    region: list[float] | None,
    source: Path,
) -> None:
    """(Optional) cut ``region`` out of ``source`` and run tilemaker with the profile into ``out``."""
    settings = get_settings()
    if shutil.which(settings.tilemaker_bin) is None:
        raise RuntimeError("Brak programu tilemaker (apt install tilemaker)")
    profile = get_profile(settings, profile_id)
    cancel = lambda: queue.is_cancel_requested(job["id"])  # noqa: E731
    pbf = source
    region_pbf = mdir / "region.tmp.osm.pbf"
    try:
        if region:
            if shutil.which(settings.osmium_bin) is None:
                raise RuntimeError("Brak programu osmium (apt install osmium-tool)")
            queue.update(job["id"], progress=0.1, message="Wycinanie obszaru (osmium)")
            extract_region(settings, source, region_pbf, tuple(region), mdir, emit, cancel)  # type: ignore[arg-type]
            pbf = region_pbf
        queue.update(
            job["id"], progress=0.3, message=f"Generowanie kafelków wektorowych (profil: {profile.name})"
        )
        tmp = out.with_suffix(".tmp.mbtiles")
        tmp.unlink(missing_ok=True)
        run_tilemaker(
            settings, pbf, tmp, mdir, emit, cancel, config=profile.config, process=profile.process
        )
        tmp.replace(out)
    finally:
        region_pbf.unlink(missing_ok=True)


def handle_import(job: dict[str, Any]) -> None:
    """Import into ``*.new`` files; the previous import is archived, never deleted."""
    settings = get_settings()
    map_id = job["map_id"]
    params = job.get("params") or {}
    profile_id = params.get("profile", DEFAULT_PROFILE)
    region = params.get("region")
    mdir = map_dir(map_id)
    pbf = mdir / "source.osm.pbf"
    out = mdir / "vector.new.mbtiles"
    cov_new = mdir / "coverage.new.json"
    logfile = mdir / "import.new.log"
    with db.connect() as c:
        current = db.row_to_dict(c.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()) or {}
    _set_map(map_id, status="importing", message=None)

    try:
        with logfile.open("w", encoding="utf-8") as lf:
            def emit(line: str) -> None:
                lf.write(line + "\n")
                lf.flush()

            emit(f"profile: {profile_id}, region: {region}")
            queue.update(job["id"], progress=0.05, message="Odczyt zasięgu pliku")
            if shutil.which(settings.osmium_bin) is None:
                raise RuntimeError("Brak programu osmium (apt install osmium-tool)")
            bbox = read_bbox(pbf, settings.osmium_bin)
            emit(f"bbox: {bbox}")

            _tilemaker_with_profile(job, mdir, emit, out, profile_id, region, pbf)

            queue.update(job["id"], progress=0.95, message="Maska pokrycia")
            tiles = coverage_from_mbtiles(out, settings.coverage_zoom)
            if not tiles and (region or bbox):  # empty or unusual output -> fall back to bbox
                tiles = list(xyz.bbox_to_tiles(tuple(region) if region else bbox, settings.coverage_zoom))
            cov_new.write_text(
                json.dumps({"zoom": settings.coverage_zoom, "tiles": tiles}), encoding="utf-8"
            )
            emit(f"coverage: {len(tiles)} tiles at z{settings.coverage_zoom}")
    except BaseException:
        for leftover in (out, cov_new, out.with_suffix(".tmp.mbtiles")):
            leftover.unlink(missing_ok=True)
        raise

    # Success: the previous import is moved to imports/<id>/, the new one becomes active.
    archive.archive_active(mdir, current)
    out.replace(mdir / archive.VECTOR)
    cov_new.replace(mdir / archive.COVERAGE)
    logfile.replace(mdir / archive.LOG)
    archive.write_active_meta(mdir, profile_id, region, bbox)
    _set_map(
        map_id,
        status="ready",
        message=None,
        bbox=json.dumps(bbox) if bbox else None,
        profile=profile_id,
        region=json.dumps(region) if region else None,
    )


def handle_sample(job: dict[str, Any]) -> None:
    """Small trial import of one profile, so the user can see it before committing.

    Writes ``sample.mbtiles`` + ``sample.json`` and never touches the map's
    real import or status.
    """
    map_id = job["map_id"]
    params = job["params"]
    mdir = map_dir(map_id)
    out = mdir / "sample.mbtiles"
    with (mdir / "sample.log").open("w", encoding="utf-8") as lf:
        def emit(line: str) -> None:
            lf.write(line + "\n")
            lf.flush()

        _tilemaker_with_profile(
            job, mdir, emit, out, params["profile"], params["bbox"], mdir / "source.osm.pbf"
        )
    (mdir / "sample.json").write_text(
        json.dumps({"profile": params["profile"], "bbox": params["bbox"]}), encoding="utf-8"
    )


# --------------------------------------------------------------------- render

def output_path(root: Path, z: int, x: int, y: int, scheme: str, fmt: str) -> Path:
    yy = xyz.xyz_to_tms_y(y, z) if scheme == "tms" else y
    return root / str(z) / str(x) / f"{yy}.{FORMATS[fmt]}"


def handle_render(job: dict[str, Any]) -> None:
    settings = get_settings()
    p = job["params"]
    map_id = job["map_id"]
    mdir = map_dir(map_id)
    root = settings.output_dir / job["id"]
    root.mkdir(parents=True, exist_ok=True)

    grid_z = int(p["grid_zoom"])
    zooms = list(range(int(p["min_zoom"]), int(p["max_zoom"]) + 1))
    selected = [tuple(t) for t in p["tiles"]]
    plan: list[tuple[int, int, int]] = []
    for z in zooms:
        if z >= grid_z:
            tiles = xyz.expand_selection(selected, grid_z, z)
        else:
            tiles = xyz.regrid_selection(selected, grid_z, z)
        plan.extend((z, x, y) for x, y in sorted(tiles))

    fmt, scheme = p["format"], p.get("scheme", "xyz")
    req = RenderRequest(
        mbtiles=mdir / "vector.mbtiles",
        style_path=settings.styles_dir / p["style"] / "style.json",
        tile_px=int(p["tile_px"]),
        fmt=fmt,
        quality=int(p.get("quality", 85)),
    )
    engine = get_engine(settings.render_engine)
    engine.open(req)
    total, done, last = len(plan), 0, 0.0
    try:
        for z, x, y in plan:
            dest = output_path(root, z, x, y, scheme, fmt)
            if not dest.exists():  # resume support
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = encode(engine.render_image(z, x, y), fmt, req.quality)
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(dest)
            done += 1
            if time.monotonic() - last > 1.0 or done == total:
                last = time.monotonic()
                queue.update(job["id"], progress=done / total, message=f"{done} / {total} kafelków")
                if queue.is_cancel_requested(job["id"]):
                    raise InterruptedError("cancelled")
    finally:
        engine.close()

    meta = {
        "job_id": job["id"],
        "map_id": map_id,
        "style": p["style"],
        "min_zoom": zooms[0],
        "max_zoom": zooms[-1],
        "tile_size": req.tile_px,
        "format": fmt,
        "scheme": scheme,
        "tiles": total,
        "engine": engine.name,
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (root / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


HANDLERS = {"import": handle_import, "render": handle_render, "sample": handle_sample}


def _import_failed(job: dict[str, Any], message: str) -> None:
    """A failed import keeps the previous active import usable."""
    map_id = job["map_id"]
    if (map_dir(map_id) / archive.VECTOR).exists():
        _set_map(map_id, status="ready", message=message)
    else:
        _set_map(map_id, status="failed", message=message)


def run_job(job: dict[str, Any]) -> None:
    try:
        HANDLERS[job["kind"]](job)
        queue.update(job["id"], status="done", progress=1.0)
    except InterruptedError:
        queue.update(job["id"], status="cancelled", message="Anulowano")
        if job["kind"] == "import" and job["map_id"]:
            _import_failed(job, "Import anulowany")
    except Exception as exc:  # noqa: BLE001 - report every failure to the UI
        log.exception("job %s failed", job["id"])
        queue.update(job["id"], status="failed", message=str(exc))
        if job["kind"] == "import" and job["map_id"]:
            _import_failed(job, str(exc))
