"""Modular ("custom") import: module catalogue, generated tilemaker setup, API, archive."""

import json
import re

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app import db
from app.importer import exports as ex
from app.importer import modules as mods
from app.importer.profiles import get_profile
from app.jobs import queue
from app.main import app

from test_library import _export, _fake_tools, _source


def _run(job_id):
    from app.jobs import handlers

    job = queue.claim_next()
    while job and job["id"] != job_id:
        job = queue.claim_next()
    handlers.run_job(job)


def test_normalize_validates_and_orders():
    assert mods.normalize(["hiking", "places", "places"]) == ["places", "hiking"]
    assert mods.normalize(None) == list(mods.DEFAULT_MODULES)
    assert mods.normalize([]) == []
    try:
        mods.normalize(["nope"])
    except ValueError:
        pass
    else:
        raise AssertionError


def test_generated_config_has_exactly_the_layers_of_the_modules(tmp_path):
    p = get_profile(get_settings(), "custom")
    config, process = mods.build(p.config, p.process, ["poi", "hiking"], tmp_path)
    layers = set(json.loads(config.read_text(encoding="utf-8"))["layers"])
    assert layers == {"transportation", "water", "waterway", "landcover", "poi", "hiking"}
    assert process.read_text(encoding="utf-8").startswith("MODULES = { poi = true, hiking = true }\n")
    config2, _ = mods.build(p.config, p.process, [], tmp_path)
    assert set(json.loads(config2.read_text(encoding="utf-8"))["layers"]) == set(mods.BASE_LAYERS)


def test_template_only_writes_layers_of_the_catalogue():
    """Every Layer("x") in the Lua must be a layer some module (or the base) provides."""
    p = get_profile(get_settings(), "custom")
    lua = p.process.read_text(encoding="utf-8")
    used = set(re.findall(r'Layer(?:AsCentroid)?\("([a-z_]+)"', lua))
    provided = set(mods.BASE_LAYERS) | {l for m in mods.MODULES for l in m.layers}
    assert used == provided
    flags = set(re.findall(r"\bM\.([a-z_]+)", lua))
    assert flags == {m.id for m in mods.MODULES}


def test_modules_endpoint_and_export_request():
    with TestClient(app) as c:
        ids = {x["id"] for x in c.get("/api/modules").json()}
        assert ids == {x.id for x in mods.MODULES}
        map_id = _source()
        r = c.post(f"/api/maps/{map_id}/exports", json={"profile": "custom", "modules": ["hiking", "poi"]})
        assert r.status_code == 200, r.text
        with db.connect() as con:
            params = json.loads(con.execute("SELECT params FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()[0])
        assert params == {"profile": "custom", "modules": ["poi", "hiking"], "regions": None, "search": True}
        e = c.get(f"/api/exports/{r.json()['export_id']}").json()
        assert e["modules"] == ["poi", "hiking"] and e["name"] == "Własny (2 elem.) · cały plik"
        bad = c.post(f"/api/maps/{map_id}/exports", json={"profile": "custom", "modules": ["x"]})
        assert bad.status_code == 400
        # modules are ignored for ordinary profiles
        r = c.post(f"/api/maps/{map_id}/exports", json={"profile": "classic", "modules": ["poi"]})
        assert r.status_code == 200
        assert c.get(f"/api/exports/{r.json()['export_id']}").json()["modules"] is None


def test_exports_with_different_modules_live_side_by_side(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    seen: list = []
    real = handlers.run_tilemaker

    def spy(settings, pbf, out, work_dir, log, cancel, config=None, process=None):
        seen.append((
            set(json.loads(config.read_text(encoding="utf-8"))["layers"]),
            process.read_text(encoding="utf-8").splitlines()[0],
        ))
        real(settings, pbf, out, work_dir, log, cancel, config=config, process=process)

    monkeypatch.setattr(handlers, "run_tilemaker", spy)
    with TestClient(app) as c:
        map_id = _source()
        a = c.post(f"/api/maps/{map_id}/exports", json={"profile": "custom", "modules": ["peaks"]}).json()
        b = c.post(f"/api/maps/{map_id}/exports", json={"profile": "classic"}).json()
        _run(a["job_id"])
        _run(b["job_id"])
        assert seen[0] == (set(mods.BASE_LAYERS) | {"mountain_peak"}, "MODULES = { peaks = true }")
        ea, eb = c.get(f"/api/exports/{a['export_id']}").json(), c.get(f"/api/exports/{b['export_id']}").json()
        assert ea["status"] == "ready" and ea["profile"] == "custom" and ea["modules"] == ["peaks"]
        assert eb["status"] == "ready" and eb["profile"] == "classic"
        # both folders exist, generated temp files are gone, nothing was archived or overwritten
        for e in (a, b):
            d = ex.export_dir(map_id, e["export_id"])
            assert (d / "vector.mbtiles").exists()
            assert not list(d.glob("custom.tmp.*"))
        assert not (get_settings().maps_dir / map_id / "imports").exists()
        assert len(c.get(f"/api/maps/{map_id}").json()["exports"]) == 2


def test_style_tile_url_changes_with_the_export_file():
    import os

    with TestClient(app) as c:
        map_id = _source()
        exp = _export(map_id)
        st = c.get(f"/api/exports/{exp}/style/classic.json").json()["sources"]
        t1 = next(iter(st.values()))["tiles"][0]
        assert "?v=" in t1
        f = ex.export_dir(map_id, exp) / "vector.mbtiles"
        os.utime(f, (1_700_000_000, 1_700_000_000))
        t2 = next(iter(c.get(f"/api/exports/{exp}/style/classic.json").json()["sources"].values()))["tiles"][0]
        assert t2.endswith("?v=1700000000") and t1 != t2
