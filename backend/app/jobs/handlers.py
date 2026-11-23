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
from ..importer import exports, modules as modules_mod
from ..importer.profiles import get_profile
from ..importer.tilemaker import extract_region, run_tilemaker
from ..osm import search as search_mod
from ..osm.analyze import coverage_from_mbtiles, read_bbox
from ..render.base import FORMATS, RenderRequest, encode, get_engine
from ..tiles import xyz
from . import queue

log = logging.getLogger("osm-tilegen.jobs")


def map_dir(map_id: str) -> Path:
    return get_settings().maps_dir / map_id


# --------------------------------------------------------------------- import

def _tilemaker_with_profile(
    job: dict[str, Any],
    mdir: Path,
    emit: Any,
    out: Path,
    profile_id: str,
    areas: list[list[float]] | None,
    source: Path,
    modules: list[str] | None = None,
) -> None:
    """(Optional) cut ``areas`` (rectangles) out of ``source` and run tilemaker with the profile into ``out``."""
    settings = get_settings()
    if shutil.which(settings.tilemaker_bin) is None:
        raise RuntimeError("Brak programu tilemaker (apt install tilemaker)")
    profile = get_profile(settings, profile_id)
    cancel = lambda: queue.is_cancel_requested(job["id"])  # noqa: E731
    pbf = source
    region_pbf = mdir / "region.tmp.osm.pbf"
    config, process = profile.config, profile.process
    generated: list[Path] = []
    try:
        if profile.id == modules_mod.CUSTOM_PROFILE:
            config, process = modules_mod.build(profile.config, profile.process, modules or [], mdir)
            generated = [config, process]
        if areas:
            if shutil.which(settings.osmium_bin) is None:
                raise RuntimeError("Brak programu osmium (apt install osmium-tool)")
            queue.update(job["id"], progress=0.1, message="Wycinanie obszaru (osmium)")
            extract_region(settings, source, region_pbf, [tuple(a) for a in areas], mdir, emit, cancel)  # type: ignore[arg-type]
            pbf = region_pbf
        queue.update(
            job["id"], progress=0.3, message=f"Generowanie kafelków wektorowych (profil: {profile.name})"
        )
        tmp = out.with_suffix(".tmp.mbtiles")
        tmp.unlink(missing_ok=True)
        run_tilemaker(
            settings, pbf, tmp, mdir, emit, cancel, config=config, process=process
        )
        tmp.replace(out)
    finally:
        region_pbf.unlink(missing_ok=True)
        (mdir / "region.tmp.geojson").unlink(missing_ok=True)
        for g in generated:
            g.unlink(missing_ok=True)


def _set_export(export_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE exports SET {cols} WHERE id = ?", (*fields.values(), export_id))


def handle_import(job: dict[str, Any]) -> None:
    """Build one export folder (vector MBTiles, coverage mask, log) from a source PBF."""
    settings = get_settings()
    map_id = job["map_id"]
    export_id = job.get("export_id")
    exp = exports.get_export(export_id) if export_id else None
    if exp is None:
        raise RuntimeError("Zadanie importu bez eksportu (stary format), utwórz eksport od nowa")
    params = job.get("params") or {}
    profile_id = params.get("profile", exp["profile"])
    modules = params.get("modules") if profile_id == modules_mod.CUSTOM_PROFILE else None
    areas = params.get("regions") or None
    mdir = map_dir(map_id)
    edir = exports.export_dir(map_id, export_id)
    edir.mkdir(parents=True, exist_ok=True)
    pbf = mdir / "source.osm.pbf"
    out = edir / exports.VECTOR
    cov = edir / exports.COVERAGE
    _set_export(export_id, status="importing", message=None)

    try:
        with (edir / exports.LOG).open("w", encoding="utf-8") as lf:
            def emit(line: str) -> None:
                lf.write(line + "\n")
                lf.flush()

            emit(f"profile: {profile_id}, modules: {modules}, areas: {areas}")
            bbox = exp.get("bbox")
            if not bbox:
                queue.update(job["id"], progress=0.05, message="Odczyt zasięgu pliku")
                if shutil.which(settings.osmium_bin) is None:
                    raise RuntimeError("Brak programu osmium (apt install osmium-tool)")
                bbox = read_bbox(pbf, settings.osmium_bin)
                bbox = list(bbox) if bbox else None
            emit(f"bbox: {bbox}")

            _tilemaker_with_profile(job, edir, emit, out, profile_id, areas, pbf, modules)

            queue.update(job["id"], progress=0.95, message="Maska pokrycia")
            tiles = coverage_from_mbtiles(out, settings.coverage_zoom)
            if not tiles and (areas or bbox):  # empty or unusual output -> fall back to bbox
                tiles = sorted({
                    t for box in (areas or [bbox])
                    for t in xyz.bbox_to_tiles(tuple(box), settings.coverage_zoom)
                })
            cov.write_text(
                json.dumps({"zoom": settings.coverage_zoom, "tiles": tiles}), encoding="utf-8"
            )
            emit(f"coverage: {len(tiles)} tiles at z{settings.coverage_zoom}")

            if params.get("search", True):  # a failed index never fails the export itself
                queue.update(job["id"], progress=0.97, message="Indeks wyszukiwania adresów")
                try:
                    n = build_export_search(job, map_id, export_id, areas, emit)
                    emit(f"search index: {n} items")
                except InterruptedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    emit(f"search index failed: {exc}")
    except BaseException:
        # Keep the folder and its log (the user can read what went wrong), drop partial results.
        for leftover in (out, cov, out.with_suffix(".tmp.mbtiles")):
            leftover.unlink(missing_ok=True)
        raise

    _set_export(export_id, status="ready", message=None, bbox=json.dumps(bbox) if bbox else None)
    exports.write_meta(edir, {**exp, "bbox": bbox})
    with db.connect() as c:  # remember the file's extent on the source as well
        c.execute("UPDATE maps SET bbox = COALESCE(bbox, ?) WHERE id = ?", (json.dumps(bbox) if bbox else None, map_id))


def build_export_search(
    job: dict[str, Any], map_id: str, export_id: str, areas: list[list[float]] | None, emit: Any
) -> int:
    """(Re)build ``search.sqlite`` of an export from the source PBF (cut to the export's area)."""
    settings = get_settings()
    if shutil.which(settings.osmium_bin) is None:
        raise RuntimeError("Brak programu osmium (apt install osmium-tool)")
    mdir = map_dir(map_id)
    edir = exports.export_dir(map_id, export_id)
    source = mdir / "source.osm.pbf"
    if not source.exists():
        raise RuntimeError("Brak pliku źródłowego PBF (został usunięty), nie można zbudować indeksu")
    cancel = lambda: queue.is_cancel_requested(job["id"])  # noqa: E731
    region = edir / "search.region.tmp.osm.pbf"
    try:
        pbf = source
        if areas:
            extract_region(settings, source, region, [tuple(a) for a in areas], edir, emit, cancel)  # type: ignore[arg-type]
            pbf = region
        return search_mod.build_index(pbf, edir / search_mod.INDEX_NAME, settings.osmium_bin, edir, emit, cancel)
    finally:
        region.unlink(missing_ok=True)
        (edir / "region.tmp.geojson").unlink(missing_ok=True)


def handle_geocode(job: dict[str, Any]) -> None:
    """Build the address search index of an existing export."""
    exp = exports.get_export(job["export_id"]) if job.get("export_id") else None
    if exp is None:
        raise RuntimeError("Eksport nie istnieje")
    edir = exports.export_dir(job["map_id"], exp["id"])
    queue.update(job["id"], progress=0.1, message="Budowanie indeksu wyszukiwania")
    with (edir / "search.log").open("w", encoding="utf-8") as lf:
        def emit(line: str) -> None:
            lf.write(line + "\n")
            lf.flush()

        n = build_export_search(job, job["map_id"], exp["id"], exp.get("regions"), emit)
        emit(f"index: {n} items")
    queue.update(job["id"], message=f"Zindeksowano {n} pozycji")


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
            job, mdir, emit, out, params["profile"], [params["bbox"]], mdir / "source.osm.pbf",
            params.get("modules"),
        )
    (mdir / "sample.json").write_text(
        json.dumps({"profile": params["profile"], "bbox": params["bbox"], "modules": params.get("modules")}), encoding="utf-8"
    )


# --------------------------------------------------------------------- render

def output_path(root: Path, z: int, x: int, y: int, scheme: str, fmt: str) -> Path:
    yy = xyz.xyz_to_tms_y(y, z) if scheme == "tms" else y
    return root / str(z) / str(x) / f"{yy}.{FORMATS[fmt]}"


def handle_render(job: dict[str, Any]) -> None:
    settings = get_settings()
    p = job["params"]
    map_id = job["map_id"]
    export_id = p["export_id"]
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
        mbtiles=exports.export_dir(map_id, export_id) / exports.VECTOR,
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
        "export_id": export_id,
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


HANDLERS = {
    "import": handle_import, "render": handle_render, "sample": handle_sample, "geocode": handle_geocode,
}


def _import_failed(job: dict[str, Any], message: str) -> None:
    """A failed export stays in the list as 'failed' (with its log) until the user deletes it."""
    if job.get("export_id"):
        _set_export(job["export_id"], status="failed", message=message)


def run_job(job: dict[str, Any]) -> None:
    try:
        HANDLERS[job["kind"]](job)
        queue.update(job["id"], status="done", progress=1.0)
    except InterruptedError:
        queue.update(job["id"], status="cancelled", message="Anulowano")
        if job["kind"] == "import":
            _import_failed(job, "Import anulowany")
    except Exception as exc:  # noqa: BLE001 - report every failure to the UI
        log.exception("job %s failed", job["id"])
        queue.update(job["id"], status="failed", message=str(exc))
        if job["kind"] == "import":
            _import_failed(job, str(exc))
