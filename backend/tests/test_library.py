"""Map library: profiles, area-limited import, sample preview, contents and part deletion."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.core.config import get_settings
from app.importer import exports as ex
from app.importer.profiles import get_profile, list_profiles
from app.importer.tilemaker import build_command
from app.jobs import queue
from app.main import app


def _source(source: bool = True) -> str:
    """A source (uploaded .osm.pbf) without any export."""
    s = get_settings()
    s.ensure_dirs()
    db.init_db()
    map_id = db.new_id()
    mdir = s.maps_dir / map_id
    mdir.mkdir(parents=True)
    if source:
        (mdir / "source.osm.pbf").write_bytes(b"x" * 1000)
    with db.connect() as c:
        c.execute(
            "INSERT INTO maps (id, name, filename, size_bytes, status, bbox, created_at)"
            " VALUES (?, 'lib', 'lib.osm.pbf', 1000, 'uploaded', ?, ?)",
            (map_id, json.dumps([18.0, 49.0, 21.0, 51.0]), db.now()),
        )
    return map_id


def _export(map_id: str, status: str = "ready", profile: str = "classic", name: str = "exp") -> str:
    """An export folder with a minimal vector MBTiles, coverage and log."""
    exp_id = db.new_id()
    edir = ex.export_dir(map_id, exp_id)
    edir.mkdir(parents=True)
    if status == "ready":
        with closing(sqlite3.connect(edir / "vector.mbtiles")) as conn:
            conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
            conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
        (edir / "coverage.json").write_text(json.dumps({"zoom": 10, "tiles": []}))
        (edir / "import.log").write_text("log")
    with db.connect() as c:
        c.execute(
            "INSERT INTO exports (id, map_id, name, status, profile, bbox, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (exp_id, map_id, name, status, profile, json.dumps([18.0, 49.0, 21.0, 51.0]), db.now()),
        )
    return exp_id


def _map_with_files(status: str = "ready", source: bool = True) -> str:
    """Compatibility helper: a source (and, for status 'ready', one export)."""
    map_id = _source(source)
    if status == "ready":
        _export(map_id)
    return map_id


def test_bundled_profiles_have_required_files():
    profiles = list_profiles(get_settings())
    ids = [p.id for p in profiles]
    assert ids[0] == "classic" and "tourist" in ids
    for p in profiles:
        assert p.config.is_file() and p.process.is_file()
        json.loads(p.config.read_text(encoding="utf-8"))  # valid JSON


def test_unknown_or_unsafe_profile_rejected():
    for bad in ("nope", "../classic", "Classic"):
        try:
            get_profile(get_settings(), bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_tilemaker_command_uses_profile_files():
    s = get_settings()
    p = get_profile(s, "tourist")
    cmd = build_command(s, Path("in.pbf"), Path("out.mbtiles"), Path("store"), p.config, p.process)
    assert cmd[cmd.index("--config") + 1] == str(p.config)
    assert cmd[cmd.index("--process") + 1] == str(p.process)


def test_compute_threads():
    from app.core.config import compute_threads

    assert compute_threads(0, 80, 4) == 3
    assert compute_threads(0, 80, 1) == 1
    assert compute_threads(0, 80, 16) == 12
    assert compute_threads(0, 0, 8) == 1  # percent clamped to >= 1
    assert compute_threads(0, 500, 8) == 8  # percent clamped to <= 100
    assert compute_threads(2, 80, 8) == 2  # explicit wins
    assert compute_threads(32, 80, 8) == 8  # explicit capped at cores


def test_tilemaker_gets_thread_limit():
    s = get_settings()
    cmd = build_command(s, Path("in.pbf"), Path("out.mbtiles"), Path("store"))
    assert cmd[cmd.index("--threads") + 1] == str(s.worker_threads)


def test_profiles_endpoint_and_style_requirements():
    with TestClient(app) as c:
        names = {p["id"] for p in c.get("/api/profiles").json()}
        assert {"classic", "tourist"} <= names
        styles = {s["id"]: s for s in c.get("/api/styles").json()}
        assert styles["basic"]["profiles"] == []
        assert styles["tourist"]["profiles"] == ["tourist", "custom"]


def test_create_export_makes_folder_row_and_job():
    with TestClient(app) as c:
        map_id = _source()
        r = c.post(f"/api/maps/{map_id}/exports", json={
            "profile": "tourist", "name": "Tatry", "bboxes": [[19.0, 49.5, 19.5, 49.8]],
        })
        assert r.status_code == 200, r.text
        exp_id = r.json()["export_id"]
        assert ex.export_dir(map_id, exp_id).is_dir()
        with db.connect() as conn:
            job = db.row_to_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone())
        assert job["kind"] == "import" and job["export_id"] == exp_id and job["map_id"] == map_id
        assert job["params"] == {"profile": "tourist", "modules": None, "regions": [[19.0, 49.5, 19.5, 49.8]], "search": True}
        e = c.get(f"/api/maps/{map_id}").json()["exports"][0]
        assert e["name"] == "Tatry" and e["status"] == "importing" and e["region"] == [19.0, 49.5, 19.5, 49.8]
        # several exports of one source may exist (and be queued) at once
        r2 = c.post(f"/api/maps/{map_id}/exports", json={"profile": "classic"})
        assert r2.status_code == 200
        names = [x["name"] for x in c.get(f"/api/maps/{map_id}").json()["exports"]]
        assert len(names) == 2 and "Klasyczna · cały plik" in names


def test_export_request_validation():
    with TestClient(app) as c:
        map_id = _source()
        assert c.post(f"/api/maps/{map_id}/exports", json={"profile": "nope"}).status_code == 400
        bad = c.post(f"/api/maps/{map_id}/exports", json={"profile": "classic", "bboxes": [[20, 50, 19, 51]]})
        assert bad.status_code == 400  # west > east
        no_src = _source(source=False)
        assert c.post(f"/api/maps/{no_src}/exports", json={"profile": "classic"}).status_code == 409
        assert c.post("/api/maps/zzzz/exports", json={"profile": "classic"}).status_code == 404


def test_overlapping_rectangles_are_merged_in_one_export():
    with TestClient(app) as c:
        map_id = _source()
        r = c.post(f"/api/maps/{map_id}/exports", json={
            "profile": "classic", "bboxes": [[18, 49, 19, 50], [18.5, 49.5, 20, 51], [30, 40, 31, 41]],
        })
        assert r.status_code == 200
        e = c.get(f"/api/maps/{map_id}").json()["exports"][0]
        assert sorted(e["regions"]) == [[18, 49, 20, 51], [30, 40, 31, 41]]
        assert e["region"] == [18, 40, 31, 51]


def test_sample_area_is_clamped_to_small_span():
    with TestClient(app) as c:
        map_id = _source()
        r = c.post(f"/api/maps/{map_id}/sample", json={"profile": "tourist", "bbox": [18.0, 49.0, 21.0, 51.0]})
        assert r.status_code == 200, r.text
        w, s, e, n = r.json()["bbox"]
        assert round(e - w, 3) == 0.1 and round(n - s, 3) == 0.1
        assert abs((w + e) / 2 - 19.5) < 1e-6 and abs((s + n) / 2 - 50.0) < 1e-6
        # only one sample at a time
        again = c.post(f"/api/maps/{map_id}/sample", json={"profile": "tourist", "bbox": [19, 50, 19.01, 50.01]})
        assert again.status_code == 409
        assert c.post(f"/api/maps/{map_id}/sample", json={"profile": "classic"}).status_code in (400, 409, 422)


def test_sample_does_not_touch_exports_and_is_served_separately():
    with TestClient(app) as c:
        map_id = _map_with_files()
        mdir = get_settings().maps_dir / map_id
        assert c.get(f"/api/maps/{map_id}/sample").json() == {"exists": False}
        with closing(sqlite3.connect(mdir / "sample.mbtiles")) as conn:
            conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
            conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
        (mdir / "sample.json").write_text(json.dumps({"profile": "tourist", "bbox": [1, 2, 3, 4]}))
        assert c.get(f"/api/maps/{map_id}/sample").json()["profile"] == "tourist"
        st = c.get(f"/api/maps/{map_id}/sample/style/tourist.json").json()
        src = next(iter(st["sources"].values()))
        assert f"/api/maps/{map_id}/sample/tiles/{{z}}/{{x}}/{{y}}.pbf" in src["tiles"][0]
        assert c.get(f"/api/maps/{map_id}/sample/tiles/1/0/0.pbf").status_code == 204
        assert c.delete(f"/api/maps/{map_id}/sample").json() == {"ok": True}
        assert c.get(f"/api/maps/{map_id}/sample").json() == {"exists": False}
        assert c.get(f"/api/maps/{map_id}/sample/tiles/1/0/0.pbf").status_code == 404
        assert len(c.get(f"/api/maps/{map_id}").json()["exports"]) == 1


def test_contents_and_deleting_source_keeps_exports():
    with TestClient(app) as c:
        map_id = _source()
        e1, e2 = _export(map_id, name="a"), _export(map_id, name="b")
        contents = c.get(f"/api/maps/{map_id}/contents").json()
        assert contents["source_bytes"] == 1000
        assert {e["id"] for e in contents["exports"]} == {e1, e2}
        assert all(e["bytes"] > 0 for e in contents["exports"])

        assert c.delete(f"/api/maps/{map_id}/parts/source").json()["freed_bytes"] == 1000
        assert not (get_settings().maps_dir / map_id / "source.osm.pbf").exists()
        assert c.get(f"/api/exports/{e1}").json()["status"] == "ready"
        # without the PBF no new export can be made
        assert c.post(f"/api/maps/{map_id}/exports", json={"profile": "classic"}).status_code == 409


def test_deleting_one_export_leaves_the_others_and_the_source():
    with TestClient(app) as c:
        map_id = _source()
        e1, e2 = _export(map_id), _export(map_id)
        r = c.delete(f"/api/exports/{e1}")
        assert r.status_code == 200 and r.json()["freed_bytes"] > 0
        assert not ex.export_dir(map_id, e1).exists()
        assert ex.export_dir(map_id, e2).is_dir()
        assert c.get(f"/api/exports/{e1}").status_code == 404
        assert (get_settings().maps_dir / map_id / "source.osm.pbf").exists()
        assert [e["id"] for e in c.get(f"/api/maps/{map_id}").json()["exports"]] == [e2]


def test_export_delete_asks_before_removing_results():
    s = get_settings()
    with TestClient(app) as c:
        map_id = _source()
        exp = _export(map_id)
        out = s.output_dir / "job123"
        out.mkdir(parents=True)
        (out / "metadata.json").write_text(json.dumps({"job_id": "job123", "map_id": map_id, "export_id": exp}))
        (out / "0.png").write_bytes(b"png")
        assert c.get(f"/api/maps/{map_id}").json()["exports"][0]["outputs"] == 1
        assert c.delete(f"/api/exports/{exp}").json()["removed_outputs"] == 0
        assert out.exists()  # results stay unless explicitly requested
        exp2 = _export(map_id)
        out2 = s.output_dir / "job456"
        out2.mkdir(parents=True)
        (out2 / "metadata.json").write_text(json.dumps({"job_id": "job456", "map_id": map_id, "export_id": exp2}))
        assert c.delete(f"/api/exports/{exp2}?outputs=true").json()["removed_outputs"] == 1
        assert not out2.exists() and out.exists()


def test_deletion_refused_while_jobs_active():
    with TestClient(app) as c:
        map_id = _source()
        exp = _export(map_id)
        queue.enqueue("render", {}, map_id=map_id, export_id=exp)
        assert c.delete(f"/api/exports/{exp}").status_code == 409
        assert c.delete(f"/api/maps/{map_id}/parts/source").status_code == 409
        assert c.delete(f"/api/maps/{map_id}").status_code == 409


def _fake_tools(monkeypatch, calls: list):
    """Replace osmium/tilemaker with stand-ins that record how they were called."""
    from app.jobs import handlers

    monkeypatch.setattr(handlers.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(handlers, "read_bbox", lambda *a, **k: (18.0, 49.0, 21.0, 51.0))

    def fake_extract(settings, src, dst, bbox, work_dir, log, cancel):
        calls.append(("extract", tuple(bbox)))
        Path(dst).write_bytes(b"region")

    def fake_tilemaker(settings, pbf, out, work_dir, log, cancel, config=None, process=None):
        calls.append(("tilemaker", Path(pbf).name, Path(config).parent.name, Path(process).parent.name))
        with closing(sqlite3.connect(out)) as conn:
            conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
            conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
            conn.execute("INSERT INTO tiles VALUES (10, 571, 688, x'00')")
            conn.commit()

    monkeypatch.setattr(handlers, "extract_region", fake_extract)
    monkeypatch.setattr(handlers, "run_tilemaker", fake_tilemaker)
    return handlers


def _run_job(handlers, job_id):
    job = queue.claim_next()
    while job and job["id"] != job_id:
        job = queue.claim_next()
    handlers.run_job(job)


def test_import_handler_fills_the_export_folder(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _source()
        region = [19.0, 49.5, 19.5, 49.8]
        r = c.post(f"/api/maps/{map_id}/exports", json={"profile": "tourist", "bboxes": [region], "search": False})
        _run_job(handlers, r.json()["job_id"])

        assert calls == [("extract", (tuple(region),)), ("tilemaker", "region.tmp.osm.pbf", "tourist", "tourist")]
        exp_id = r.json()["export_id"]
        e = c.get(f"/api/exports/{exp_id}").json()
        assert e["status"] == "ready" and e["profile"] == "tourist" and e["region"] == region
        edir = ex.export_dir(map_id, exp_id)
        assert {p.name for p in edir.iterdir()} >= {"vector.mbtiles", "coverage.json", "import.log", "meta.json"}
        assert json.loads((edir / "meta.json").read_text())["profile"] == "tourist"
        assert not (edir / "region.tmp.osm.pbf").exists()  # temporary cut-out is removed
        assert c.get(f"/api/exports/{exp_id}/coverage").json()["zoom"] == 10
        assert "profile: tourist" in c.get(f"/api/exports/{exp_id}/log").json()["log"]


def test_import_handler_whole_file_default_profile(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _source()
        r = c.post(f"/api/maps/{map_id}/exports", json={"search": False})
        _run_job(handlers, r.json()["job_id"])
        assert calls == [("tilemaker", "source.osm.pbf", "classic", "classic")]
        e = c.get(f"/api/exports/{r.json()['export_id']}").json()
        assert e["status"] == "ready" and e["profile"] == "classic" and e["region"] is None


def test_failed_import_keeps_a_failed_export_with_its_log(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)

    def boom(*a, **k):
        raise RuntimeError("tilemaker exploded")

    monkeypatch.setattr(handlers, "run_tilemaker", boom)
    with TestClient(app) as c:
        map_id = _source()
        ok = _export(map_id)  # an earlier export is not affected
        r = c.post(f"/api/maps/{map_id}/exports", json={})
        _run_job(handlers, r.json()["job_id"])
        e = c.get(f"/api/exports/{r.json()['export_id']}").json()
        assert e["status"] == "failed" and "exploded" in e["message"]
        assert ex.export_dir(map_id, ok).joinpath("vector.mbtiles").exists()
        assert not ex.export_dir(map_id, e["id"]).joinpath("vector.mbtiles").exists()
        assert c.get(f"/api/exports/{e['id']}/log").status_code == 200


def test_sample_handler_leaves_exports_untouched(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _map_with_files()
        bbox = [19.0, 49.5, 19.1, 49.6]
        job_id = queue.enqueue("sample", {"profile": "tourist", "bbox": bbox}, map_id=map_id)
        _run_job(handlers, job_id)
        mdir = get_settings().maps_dir / map_id
        assert (mdir / "sample.mbtiles").exists()
        assert json.loads((mdir / "sample.json").read_text()) == {"profile": "tourist", "bbox": bbox, "modules": None}
        exports = c.get(f"/api/maps/{map_id}").json()["exports"]
        assert len(exports) == 1 and exports[0]["status"] == "ready"


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for x in node:
            yield from _all_strings(x)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)


def test_every_style_is_self_contained_and_offline():
    s = get_settings()
    profile_ids = {p.id for p in list_profiles(s)}
    style_dirs = [d for d in s.styles_dir.iterdir() if (d / "style.json").is_file()]
    assert {d.name for d in style_dirs} >= {"basic", "classic", "tourist", "bright", "positron", "darkmatter", "liberty"}
    for d in style_dirs:
        raw = (d / "style.json").read_text(encoding="utf-8")
        doc = json.loads(raw)
        # no external services: the app injects tiles, glyphs and sprite itself
        assert "maptiler" not in raw and "github.io" not in raw, d.name
        assert "glyphs" not in doc and "sprite" not in doc, d.name
        assert {v["type"] for v in doc["sources"].values()} == {"vector"}, d.name
        needed = set(doc.get("metadata", {}).get("osm-tilegen", {}).get("profiles", []))
        assert needed <= profile_ids, (d.name, needed)
        # fonts: every named font stack exists with Latin + Latin Extended-A (Polish letters)
        for layer in doc["layers"]:
            for font in _all_strings(layer.get("layout", {}).get("text-font", [])):
                if " " in font:
                    assert (s.styles_dir / "fonts" / font / "256-511.pbf").is_file(), (d.name, font)
        uses_icons = any("icon-image" in l.get("layout", {}) for l in doc["layers"])
        if uses_icons:
            for suffix in (".json", ".png", "@2x.json", "@2x.png"):
                assert (d / f"sprite{suffix}").is_file(), (d.name, suffix)
        if (d / "sprite.json").exists():
            assert (d / "LICENSE.md").is_file(), d.name  # third-party styles keep their license


def test_full_profile_is_in_registry_and_has_no_missing_shapefiles():
    full = get_profile(get_settings(), "full")
    cfg = json.loads(full.config.read_text(encoding="utf-8"))
    assert all("source" not in layer for layer in cfg["layers"].values())
    assert {"poi", "housenumber", "aeroway", "water_name", "transportation_name"} <= set(cfg["layers"])


def test_style_endpoint_injects_glyphs_sprite_and_attribution():
    with TestClient(app) as c:
        map_id = _source()
        exp = _export(map_id)
        st = c.get(f"/api/exports/{exp}/style/liberty.json").json()
        assert st["glyphs"].endswith("/styles/fonts/{fontstack}/{range}.pbf")
        assert st["sprite"].endswith("/styles/liberty/sprite")
        src = next(iter(st["sources"].values()))
        assert "OpenMapTiles" in src["attribution"] and "OpenStreetMap" in src["attribution"]
        assert f"/api/exports/{exp}/tiles/{{z}}/{{x}}/{{y}}.pbf?v=" in src["tiles"][0]
        assert c.get("/styles/liberty/sprite.json").status_code == 200
        assert c.get("/styles/liberty/sprite@2x.png").status_code == 200
        r = c.get("/styles/fonts/Noto%20Sans%20Regular/256-511.pbf")
        assert r.status_code == 200 and len(r.content) > 1000


def test_legacy_layout_is_moved_into_export_folders_without_losing_files():
    """Old layout: active import in the map folder plus imports/<id>/ archives."""
    map_id = _source()
    mdir = get_settings().maps_dir / map_id
    for name, data in (("vector.mbtiles", b"active"), ("coverage.json", b"{}"), ("import.log", b"log")):
        (mdir / name).write_bytes(data)
    (mdir / "import.json").write_text(json.dumps({"profile": "tourist", "region": [1, 2, 3, 4], "created_at": 5.0}))
    arch = mdir / "imports" / "abc123"
    arch.mkdir(parents=True)
    (arch / "vector.mbtiles").write_bytes(b"archived")
    (arch / "meta.json").write_text(json.dumps({"profile": "classic", "region": None, "created_at": 1.0}))

    lock = get_settings().data_dir / "migrate-exports.lock"
    lock.write_text("busy")
    assert ex.migrate_legacy() == 0  # another process is migrating: do nothing
    lock.unlink()
    assert ex.migrate_legacy() >= 2
    assert not lock.exists()
    assert ex.migrate_legacy() == 0  # idempotent

    with db.connect() as conn:
        rows = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM exports WHERE map_id = ?", (map_id,))]
    assert len(rows) == 2
    by_profile = {r["profile"]: r for r in rows}
    assert (ex.export_dir(map_id, by_profile["tourist"]["id"]) / "vector.mbtiles").read_bytes() == b"active"
    assert (ex.export_dir(map_id, by_profile["classic"]["id"]) / "vector.mbtiles").read_bytes() == b"archived"
    assert by_profile["tourist"]["region"] == [1, 2, 3, 4]
    assert not (mdir / "vector.mbtiles").exists() and not (mdir / "imports").exists()
    assert (mdir / "source.osm.pbf").exists()


def test_clear_finished_jobs_keeps_active_ones():
    db.init_db()
    client = TestClient(app)
    done = queue.enqueue("render", {})
    queued = queue.enqueue("render", {})
    queue.update(done, status="done")
    assert client.delete(f"/api/jobs/{queued}").status_code == 409
    assert client.delete("/api/jobs").json()["removed"] >= 1
    ids = {j["id"] for j in client.get("/api/jobs").json()}
    assert done not in ids and queued in ids


def test_merge_bboxes_joins_overlapping_only():
    from app.importer.regions import merge_bboxes, outline

    a, b, far = (0, 0, 2, 2), (1, 1, 3, 3), (10, 10, 11, 11)
    assert sorted(merge_bboxes([a, b, far])) == [(0, 0, 3, 3), (10, 10, 11, 11)]
    assert outline([a, far]) == (0, 0, 11, 11)


def _zoom_curves(node) -> int:
    """Number of zoom-based interpolate/step expressions inside one property value."""
    if not isinstance(node, list):
        return 0
    n = 0
    if node and node[0] in ("interpolate", "interpolate-hcl", "interpolate-lab") and ["zoom"] in node:
        n += 1
    if node and node[0] == "step" and len(node) > 1 and node[1] == ["zoom"]:
        n += 1
    return n + sum(_zoom_curves(x) for x in node)


def test_styles_have_at_most_one_zoom_curve_per_property():
    """MapLibre refuses the whole style (and the map keeps the previous one) otherwise."""
    s = get_settings()
    for d in s.styles_dir.iterdir():
        f = d / "style.json"
        if not f.is_file():
            continue
        for layer in json.loads(f.read_text(encoding="utf-8"))["layers"]:
            for group in ("paint", "layout"):
                for prop, value in layer.get(group, {}).items():
                    assert _zoom_curves(value) <= 1, (d.name, layer["id"], prop)
