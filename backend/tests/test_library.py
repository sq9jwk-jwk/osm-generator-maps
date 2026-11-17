"""Map library: profiles, area-limited import, sample preview, contents and part deletion."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.core.config import get_settings
from app.importer.profiles import get_profile, list_profiles
from app.importer.tilemaker import build_command
from app.jobs import queue
from app.main import app


def _map_with_files(status: str = "ready", source: bool = True) -> str:
    s = get_settings()
    s.ensure_dirs()
    db.init_db()
    map_id = db.new_id()
    mdir = s.maps_dir / map_id
    mdir.mkdir(parents=True)
    if source:
        (mdir / "source.osm.pbf").write_bytes(b"x" * 1000)
    with closing(sqlite3.connect(mdir / "vector.mbtiles")) as conn:
        conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
    (mdir / "coverage.json").write_text(json.dumps({"zoom": 10, "tiles": []}))
    (mdir / "import.log").write_text("log")
    with db.connect() as c:
        c.execute(
            "INSERT INTO maps (id, name, filename, size_bytes, status, bbox, created_at)"
            " VALUES (?, 'lib', 'lib.osm.pbf', 1000, ?, ?, ?)",
            (map_id, status, json.dumps([18.0, 49.0, 21.0, 51.0]), db.now()),
        )
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


def test_profiles_endpoint_and_style_requirements():
    with TestClient(app) as c:
        names = {p["id"] for p in c.get("/api/profiles").json()}
        assert {"classic", "tourist"} <= names
        styles = {s["id"]: s for s in c.get("/api/styles").json()}
        assert styles["basic"]["profiles"] == []
        assert styles["tourist"]["profiles"] == ["tourist"]


def test_import_with_profile_and_region_enqueues_job():
    with TestClient(app) as c:
        map_id = _map_with_files()
        r = c.post(f"/api/maps/{map_id}/import", json={"profile": "tourist", "bbox": [19.0, 49.5, 19.5, 49.8]})
        assert r.status_code == 200, r.text
        job = next(j for j in c.get("/api/jobs").json() if j["id"] == r.json()["job_id"])
        assert job["kind"] == "import"
        with db.connect() as conn:
            params = json.loads(conn.execute("SELECT params FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0])
        assert params == {"profile": "tourist", "region": [19.0, 49.5, 19.5, 49.8]}
        # a second import while the first is queued is refused
        assert c.post(f"/api/maps/{map_id}/import", json={"profile": "classic"}).status_code == 409


def test_import_validation():
    with TestClient(app) as c:
        map_id = _map_with_files()
        assert c.post(f"/api/maps/{map_id}/import", json={"profile": "nope"}).status_code == 400
        bad = c.post(f"/api/maps/{map_id}/import", json={"profile": "classic", "bbox": [20, 50, 19, 51]})
        assert bad.status_code == 400  # west > east
        no_src = _map_with_files(source=False)
        assert c.post(f"/api/maps/{no_src}/import", json={"profile": "classic"}).status_code == 409


def test_sample_area_is_clamped_to_small_span():
    with TestClient(app) as c:
        map_id = _map_with_files()
        r = c.post(f"/api/maps/{map_id}/sample", json={"profile": "tourist", "bbox": [18.0, 49.0, 21.0, 51.0]})
        assert r.status_code == 200, r.text
        w, s, e, n = r.json()["bbox"]
        assert round(e - w, 3) == 0.1 and round(n - s, 3) == 0.1
        assert abs((w + e) / 2 - 19.5) < 1e-6 and abs((s + n) / 2 - 50.0) < 1e-6
        # only one sample at a time
        again = c.post(f"/api/maps/{map_id}/sample", json={"profile": "tourist", "bbox": [19, 50, 19.01, 50.01]})
        assert again.status_code == 409
        assert c.post(f"/api/maps/{map_id}/sample", json={"profile": "classic"}).status_code in (400, 409, 422)


def test_sample_does_not_touch_map_and_is_served_as_variant():
    with TestClient(app) as c:
        map_id = _map_with_files()
        mdir = get_settings().maps_dir / map_id
        assert c.get(f"/api/maps/{map_id}/sample").json() == {"exists": False}
        with closing(sqlite3.connect(mdir / "sample.mbtiles")) as conn:
            conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
            conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
        (mdir / "sample.json").write_text(json.dumps({"profile": "tourist", "bbox": [1, 2, 3, 4]}))
        assert c.get(f"/api/maps/{map_id}/sample").json()["profile"] == "tourist"
        st = c.get(f"/api/maps/{map_id}/style/tourist.json?variant=sample").json()
        src = next(iter(st["sources"].values()))
        assert src["tiles"][0].endswith(".pbf?variant=sample")
        assert c.get(f"/api/maps/{map_id}/tiles/1/0/0.pbf?variant=evil").status_code == 400
        assert c.delete(f"/api/maps/{map_id}/sample").json() == {"ok": True}
        assert c.get(f"/api/maps/{map_id}/sample").json() == {"exists": False}
        assert c.get(f"/api/maps/{map_id}").json()["status"] == "ready"


def test_contents_and_part_deletion():
    with TestClient(app) as c:
        map_id = _map_with_files()
        contents = c.get(f"/api/maps/{map_id}/contents").json()
        keys = {p["key"]: p["bytes"] for p in contents["parts"]}
        assert keys["source"] == 1000 and "import" in keys and "log" in keys

        r = c.delete(f"/api/maps/{map_id}/parts/log")
        assert r.status_code == 200 and r.json()["freed_bytes"] == 3
        assert c.delete(f"/api/maps/{map_id}/parts/bogus").status_code == 400

        r = c.delete(f"/api/maps/{map_id}/parts/import")
        assert r.status_code == 200 and r.json()["freed_bytes"] > 0
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["status"] == "uploaded"
        assert (get_settings().maps_dir / map_id / "source.osm.pbf").exists()

        # without the PBF, deleting the import is refused (delete the whole map instead)
        r = c.delete(f"/api/maps/{map_id}/parts/source")
        assert r.status_code == 200
        r = c.delete(f"/api/maps/{map_id}/parts/import")
        assert r.status_code == 409


def test_part_deletion_refused_while_jobs_active():
    with TestClient(app) as c:
        map_id = _map_with_files()
        queue.enqueue("import", {}, map_id=map_id)
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


def test_import_handler_limits_region_and_records_profile(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _map_with_files(status="uploaded")
        region = [19.0, 49.5, 19.5, 49.8]
        job_id = queue.enqueue("import", {"profile": "tourist", "region": region}, map_id=map_id)
        job = queue.claim_next()
        while job and job["id"] != job_id:
            job = queue.claim_next()
        handlers.run_job(job)

        assert calls == [("extract", tuple(region)), ("tilemaker", "region.tmp.osm.pbf", "tourist", "tourist")]
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["status"] == "ready" and m["profile"] == "tourist" and m["region"] == region
        # the temporary cut-out is removed after the import
        assert not (get_settings().maps_dir / map_id / "region.tmp.osm.pbf").exists()


def test_import_handler_whole_file_default_profile(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _map_with_files(status="uploaded")
        job_id = queue.enqueue("import", {}, map_id=map_id)
        job = queue.claim_next()
        while job and job["id"] != job_id:
            job = queue.claim_next()
        handlers.run_job(job)
        assert calls == [("tilemaker", "source.osm.pbf", "classic", "classic")]
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["status"] == "ready" and m["profile"] == "classic" and m["region"] is None


def test_sample_handler_leaves_map_untouched(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    with TestClient(app) as c:
        map_id = _map_with_files()
        bbox = [19.0, 49.5, 19.1, 49.6]
        job_id = queue.enqueue("sample", {"profile": "tourist", "bbox": bbox}, map_id=map_id)
        job = queue.claim_next()
        while job and job["id"] != job_id:
            job = queue.claim_next()
        handlers.run_job(job)
        mdir = get_settings().maps_dir / map_id
        assert (mdir / "sample.mbtiles").exists()
        assert json.loads((mdir / "sample.json").read_text()) == {"profile": "tourist", "bbox": bbox}
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["status"] == "ready" and m["profile"] == "classic"


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
        map_id = _map_with_files()
        st = c.get(f"/api/maps/{map_id}/style/liberty.json").json()
        assert st["glyphs"].endswith("/styles/fonts/{fontstack}/{range}.pbf")
        assert st["sprite"].endswith("/styles/liberty/sprite")
        src = next(iter(st["sources"].values()))
        assert "OpenMapTiles" in src["attribution"] and "OpenStreetMap" in src["attribution"]
        assert c.get("/styles/liberty/sprite.json").status_code == 200
        assert c.get("/styles/liberty/sprite@2x.png").status_code == 200
        r = c.get("/styles/fonts/Noto%20Sans%20Regular/256-511.pbf")
        assert r.status_code == 200 and len(r.content) > 1000


def test_archived_import_is_kept_and_can_be_activated():
    from app.importer import archive

    map_id = _map_with_files()
    mdir = get_settings().maps_dir / map_id
    row = {"profile": "classic", "region": None, "bbox": [18.0, 49.0, 21.0, 51.0]}

    imp_id = archive.archive_active(mdir, row)
    assert imp_id and not (mdir / "vector.mbtiles").exists()
    assert (mdir / "imports" / imp_id / "vector.mbtiles").exists()

    client = TestClient(app)
    contents = client.get(f"/api/maps/{map_id}/contents").json()
    assert [i["id"] for i in contents["imports"]] == [imp_id]
    assert contents["imports"][0]["profile"] == "classic"

    assert client.post(f"/api/maps/{map_id}/imports/{imp_id}/activate").status_code == 200
    assert (mdir / "vector.mbtiles").exists()
    assert client.get(f"/api/maps/{map_id}/contents").json()["imports"] == []

    # activating again archives the current one instead of deleting it
    new_id = archive.archive_active(mdir, row)
    assert client.delete(f"/api/maps/{map_id}/imports/{new_id}").status_code == 200
    assert client.delete(f"/api/maps/{map_id}/imports/{new_id}").status_code == 404
    assert client.post(f"/api/maps/{map_id}/imports/..%2f/activate").status_code in (404, 405)


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
