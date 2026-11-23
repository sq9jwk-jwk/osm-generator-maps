"""End-to-end API flow with the debug render engine (no osmium/tilemaker needed)."""

import gzip
import io
import json
import sqlite3
import zipfile

from fastapi.testclient import TestClient

from app import db
from app.core.config import get_settings
from app.importer import exports as ex
from app.jobs import handlers, queue
from app.main import app
from app.tiles import xyz

client = TestClient(app)


def _fake_ready_map() -> str:
    """Insert a source with one 'ready' export (minimal vector MBTiles); returns the export id."""
    s = get_settings()
    s.ensure_dirs()
    db.init_db()
    map_id = db.new_id()
    exp_id = db.new_id()
    (s.maps_dir / map_id).mkdir(parents=True)
    edir = ex.export_dir(map_id, exp_id)
    edir.mkdir(parents=True)
    with sqlite3.connect(edir / "vector.mbtiles") as conn:
        conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
        conn.executemany("INSERT INTO metadata VALUES (?, ?)", [("minzoom", "0"), ("maxzoom", "14")])
        # z10 tile 571/335 (XYZ) -> TMS row 1023-335 = 688
        conn.execute("INSERT INTO tiles VALUES (10, 571, 688, ?)", (gzip.compress(b"fake-mvt"),))
    bbox = (20.7, 52.1, 21.2, 52.4)
    cov = [list(t) for t in xyz.bbox_to_tiles(bbox, 10)]
    (edir / "coverage.json").write_text(json.dumps({"zoom": 10, "tiles": cov}))
    with db.connect() as c:
        c.execute(
            "INSERT INTO maps (id, name, filename, size_bytes, status, bbox, created_at)"
            " VALUES (?, 'test', 'test.osm.pbf', 1, 'uploaded', ?, ?)",
            (map_id, json.dumps(bbox), db.now()),
        )
        c.execute(
            "INSERT INTO exports (id, map_id, name, status, profile, bbox, created_at)"
            " VALUES (?, ?, 'test export', 'ready', 'classic', ?, ?)",
            (exp_id, map_id, json.dumps(bbox), db.now()),
        )
    return exp_id


def test_health():
    with TestClient(app) as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["render_engine"] == "debug"


def test_chunked_upload_flow():
    with TestClient(app) as c:
        data = b"\x00\x00\x00\x0d\x0a\x09OSMHeader" + b"x" * 5000
        r = c.post("/api/uploads", json={"filename": "a.osm.pbf", "size": len(data)})
        assert r.status_code == 200, r.text
        uid = r.json()["id"]
        assert c.put(f"/api/uploads/{uid}?offset=0", content=data[:3000]).json()["received"] == 3000
        # wrong offset -> 409 with expected offset (resume)
        r = c.put(f"/api/uploads/{uid}?offset=0", content=data[3000:])
        assert r.status_code == 409
        assert c.put(f"/api/uploads/{uid}?offset=3000", content=data[3000:]).json()["received"] == len(data)
        r = c.post(f"/api/uploads/{uid}/complete")
        assert r.status_code == 200, r.text
        map_id = r.json()["map_id"]
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["status"] == "uploaded" and m["exports"] == []
        # uploading must not start an import by itself
        jobs = c.get("/api/jobs").json()
        assert not any(j["map_id"] == map_id for j in jobs)


def test_reject_wrong_extension_and_content():
    with TestClient(app) as c:
        assert c.post("/api/uploads", json={"filename": "a.osm", "size": 10}).status_code == 400
        uid = c.post("/api/uploads", json={"filename": "b.osm.pbf", "size": 4}).json()["id"]
        c.put(f"/api/uploads/{uid}?offset=0", content=b"nope")
        assert c.post(f"/api/uploads/{uid}/complete").status_code == 400


def test_preview_tile_and_style():
    with TestClient(app) as c:
        exp_id = _fake_ready_map()
        r = c.get(f"/api/exports/{exp_id}/tiles/10/571/335.pbf")
        assert r.status_code == 200
        assert c.get(f"/api/exports/{exp_id}/tiles/10/0/0.pbf").status_code == 204
        styles = c.get("/api/styles").json()
        assert any(s["id"] == "basic" for s in styles)
        st = c.get(f"/api/exports/{exp_id}/style/basic.json").json()
        src = next(iter(st["sources"].values()))
        assert f"/api/exports/{exp_id}/tiles/{{z}}/{{x}}/{{y}}.pbf?v=" in src["tiles"][0]
        assert c.get(f"/api/exports/{exp_id}/coverage").json()["zoom"] == 10


def test_render_job_and_zip():
    with TestClient(app) as c:
        exp_id = _fake_ready_map()
        bad = c.post("/api/jobs/render", json={
            "export_id": exp_id, "style": "basic", "grid_zoom": 12, "min_zoom": 11,
            "max_zoom": 11, "tiles": [[2287, 1346]],
        })
        assert bad.status_code == 422  # grid finer than output
        r = c.post("/api/jobs/render", json={
            "export_id": exp_id, "style": "basic", "grid_zoom": 12, "min_zoom": 12,
            "max_zoom": 13, "tiles": [[2287, 1346]], "tile_px": 128,
            "format": "jpg", "scheme": "tms",
        })
        assert r.status_code == 200, r.text
        assert r.json()["tiles"] == 1 + 4
        job_id = r.json()["job_id"]

        job = queue.claim_next()
        while job and job["id"] != job_id:
            handlers.run_job(job)
            job = queue.claim_next()
        assert job is not None
        handlers.run_job(job)
        assert c.get(f"/api/jobs/{job_id}").json()["status"] == "done"

        outs = c.get("/api/outputs").json()
        meta = next(o for o in outs if o["job_id"] == job_id)
        assert meta["tiles"] == 5 and meta["scheme"] == "tms"
        assert meta["export_id"] == exp_id and meta["map_id"]
        assert c.get(f"/api/exports/{exp_id}").json()["outputs"] == 1

        z = c.get(f"/api/outputs/{job_id}/zip")
        assert z.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        tms_y = (1 << 12) - 1 - 1346
        assert f"12/2287/{tms_y}.jpg" in names
        assert "metadata.json" in names
        assert sum(n.endswith(".jpg") for n in names) == 5
