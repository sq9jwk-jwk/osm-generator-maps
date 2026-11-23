"""Modular ("custom") import: module catalogue, generated tilemaker setup, API, archive."""

import json
import re

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.importer import modules as mods
from app.importer.profiles import get_profile
from app.jobs import queue
from app.main import app

from test_library import _fake_tools, _map_with_files


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


def test_modules_endpoint_and_import_request():
    with TestClient(app) as c:
        ids = {m["id"] for m in c.get("/api/modules").json()}
        assert ids == {m.id for m in mods.MODULES}
        map_id = _map_with_files(status="uploaded")
        r = c.post(f"/api/maps/{map_id}/import", json={"profile": "custom", "modules": ["hiking", "poi"]})
        assert r.status_code == 200, r.text
        with __import__("app.db", fromlist=["connect"]).connect() as con:
            params = json.loads(con.execute("SELECT params FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()[0])
        assert params == {"profile": "custom", "modules": ["poi", "hiking"], "regions": None}
        bad = _map_with_files(status="uploaded")
        assert c.post(f"/api/maps/{bad}/import", json={"profile": "custom", "modules": ["x"]}).status_code == 400
        # modules are ignored for ordinary profiles
        other = _map_with_files(status="uploaded")
        r = c.post(f"/api/maps/{other}/import", json={"profile": "classic", "modules": ["poi"]})
        assert r.status_code == 200


def test_custom_import_records_modules_and_archives_previous(monkeypatch):
    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    seen: dict = {}
    real = handlers.run_tilemaker

    def spy(settings, pbf, out, work_dir, log, cancel, config=None, process=None):
        seen["layers"] = set(json.loads(config.read_text(encoding="utf-8"))["layers"])
        seen["header"] = process.read_text(encoding="utf-8").splitlines()[0]
        real(settings, pbf, out, work_dir, log, cancel, config=config, process=process)

    monkeypatch.setattr(handlers, "run_tilemaker", spy)
    with TestClient(app) as c:
        map_id = _map_with_files(status="ready")  # has an active classic import
        mdir = get_settings().maps_dir / map_id
        job_id = queue.enqueue("import", {"profile": "custom", "modules": ["peaks"]}, map_id=map_id)
        _run(job_id)
        assert seen["layers"] == set(mods.BASE_LAYERS) | {"mountain_peak"}
        assert seen["header"] == "MODULES = { peaks = true }"
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["profile"] == "custom" and m["modules"] == ["peaks"]
        # generated temp files are cleaned up and the previous import was kept, not deleted
        assert not list(mdir.glob("custom.tmp.*"))
        archives = c.get(f"/api/maps/{map_id}/contents").json()["imports"]
        assert len(archives) == 1 and archives[0]["profile"] == "classic"
        # activating the old import restores its profile; the custom one is archived with its modules
        imp = archives[0]["id"]
        assert c.post(f"/api/maps/{map_id}/imports/{imp}/activate").status_code == 200
        m = c.get(f"/api/maps/{map_id}").json()
        assert m["profile"] == "classic" and m["modules"] is None
        archived = c.get(f"/api/maps/{map_id}/contents").json()["imports"]
        assert archived[0]["profile"] == "custom" and archived[0]["modules"] == ["peaks"]


def test_style_tile_url_changes_with_the_import_file():
    import os

    with TestClient(app) as c:
        map_id = _map_with_files()
        url1 = c.get(f"/api/maps/{map_id}/style/classic.json").json()["sources"]
        t1 = next(iter(url1.values()))["tiles"][0]
        assert "?v=" in t1
        f = get_settings().maps_dir / map_id / "vector.mbtiles"
        os.utime(f, (1_700_000_000, 1_700_000_000))
        t2 = next(iter(c.get(f"/api/maps/{map_id}/style/classic.json").json()["sources"].values()))["tiles"][0]
        assert t2.endswith("?v=1700000000") and t1 != t2
