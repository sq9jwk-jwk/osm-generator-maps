"""Offline address search: index building from OSM features, querying, API."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.importer import exports as ex
from app.main import app
from app.osm import search

from test_library import _export, _source


def pt(lon: float, lat: float, **props: str) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def way(coords: list[list[float]], **props: str) -> dict:
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


FEATURES = [
    pt(19.95, 49.29, name="Zakopane", place="town", postal_code="34-500"),
    pt(20.04, 49.31, name="Murzasichle", place="village"),
    way([[19.94, 49.29], [19.95, 49.295]], highway="residential", name="Żółkiewskiego"),
    way([[20.04, 49.31], [20.045, 49.311]], highway="residential", name="Żółkiewskiego"),  # same name, other village
    way([[19.95, 49.30], [19.96, 49.30]], highway="residential"),  # no name: ignored
    pt(19.945, 49.292, **{"addr:street": "Żółkiewskiego", "addr:housenumber": "12", "addr:postcode": "34-500",
                           "addr:city": "Zakopane"}),
    pt(19.9619, 49.3004, name="Centrum Informacji", tourism="information",
       **{"addr:street": "Chramcówki", "addr:housenumber": "35", "addr:postcode": "34-500", "addr:city": "Zakopane"}),
    pt(19.99, 49.2, name="Giewont", natural="peak"),
]


def make_index(tmp_path: Path) -> Path:
    out = tmp_path / "s.sqlite"
    search.write_index(search.collect(FEATURES), out)
    return out


def labels(index: Path, q: str, **kw) -> list[str]:
    return [r["label"] for r in search.search(index, q, **kw)]


def test_fold():
    assert search.fold("Żółkiewskiego Łódź ąęćńśźż") == "zolkiewskiego lodz aecnszz"
    assert search.tokens("34-500 Zakopane!") == ["34", "500", "zakopane"]


def test_street_without_polish_letters_and_prefix(tmp_path):
    idx = make_index(tmp_path)
    assert any(label.startswith("Żółkiewskiego") for label in labels(idx, "zolkie"))
    assert any(label.startswith("Żółkiewskiego") for label in labels(idx, "ŻÓŁKIEWSKIEGO"))


def test_same_street_name_in_two_places_gives_two_streets(tmp_path):
    idx = make_index(tmp_path)
    streets = [r for r in search.search(idx, "zolkiewskiego") if r["kind"] == "street"]
    assert len(streets) == 2
    assert {r["label"] for r in streets} == {"Żółkiewskiego, Zakopane", "Żółkiewskiego, Murzasichle"}


def test_address_with_number(tmp_path):
    idx = make_index(tmp_path)
    res = search.search(idx, "zolkiewskiego 12")
    assert res[0]["kind"] == "address"
    assert res[0]["label"] == "Żółkiewskiego 12, Zakopane"
    assert res[0]["lat"] == pytest.approx(49.292)


def test_postcode_with_and_without_dash(tmp_path):
    idx = make_index(tmp_path)
    for q in ("34-500", "34500", "34-5"):
        res = search.search(idx, q)
        assert any(r["kind"] == "postcode" and r["label"].startswith("34-500") for r in res), q


def test_place_ranks_first_and_poi_found(tmp_path):
    idx = make_index(tmp_path)
    assert search.search(idx, "zakop")[0]["kind"] == "place"
    assert labels(idx, "giew") == ["Giewont"]
    assert "Centrum Informacji" in labels(idx, "centrum")


def test_empty_and_garbage_queries(tmp_path):
    idx = make_index(tmp_path)
    assert search.search(idx, "") == []
    assert search.search(idx, "   ") == []
    assert search.search(idx, '"*() OR NEAR') == [] or isinstance(search.search(idx, '"*() OR NEAR'), list)
    assert search.search(idx, "nieistnieje") == []
    assert search.search(tmp_path / "missing.sqlite", "x") == []
    assert len(search.search(idx, "z", limit=2)) <= 2


def test_api_search_and_missing_index():
    mid = _source()
    eid = _export(mid)
    client = TestClient(app)
    assert client.get(f"/api/exports/{eid}/search?q=a").status_code == 409
    search.write_index(search.collect(FEATURES), ex.export_dir(mid, eid) / ex.SEARCH)
    r = client.get(f"/api/exports/{eid}/search", params={"q": "zakopane", "limit": 3})
    assert r.status_code == 200
    assert r.json()[0]["label"] == "Zakopane"
    assert client.get(f"/api/exports/{eid}").json()["search"] is True
    assert client.get("/api/exports/nope!/search?q=a").status_code == 400


def test_api_queues_index_job():
    mid = _source()
    eid = _export(mid)
    r = TestClient(app).post(f"/api/exports/{eid}/search/index")
    assert r.status_code == 200
    with db.connect() as c:
        row = c.execute("SELECT kind, export_id FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()
    assert row["kind"] == "geocode" and row["export_id"] == eid


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium not installed")
def test_real_pbf_end_to_end(tmp_path):
    """Build an index from the Tatra test extract with real osmium (skipped without the file)."""
    pbf = Path(__file__).resolve().parents[2] / "scripts" / "testdata" / "tatry.osm.pbf"
    if not pbf.exists():
        pytest.skip("no scripts/testdata/tatry.osm.pbf")
    out = tmp_path / "t.sqlite"
    n = search.build_index(pbf, out, "osmium", tmp_path)
    assert n > 1000
    assert any(r["kind"] == "place" for r in search.search(out, "Zakopane"))
    assert any(r["kind"] == "street" and "Krup" in r["label"] for r in search.search(out, "krupowki"))
    assert any(r["kind"] == "postcode" for r in search.search(out, "34-500"))
    assert any(r["kind"] == "address" for r in search.search(out, "Chramcowki 35"))
    assert not list(tmp_path.glob("*.tmp.*"))


def test_import_builds_search_index_and_survives_its_failure(monkeypatch):
    """A search index failure must not fail the export; success leaves search.sqlite behind."""
    from test_library import _fake_tools, _run_job

    calls: list = []
    handlers = _fake_tools(monkeypatch, calls)
    built: list = []

    def fake_build(pbf, out, osmium, work, log, cancel):
        built.append(pbf.name)
        search.write_index(search.collect(FEATURES), out)
        return 1

    monkeypatch.setattr(handlers.search_mod, "build_index", fake_build)
    with TestClient(app) as c:
        mid = _source()
        r = c.post(f"/api/maps/{mid}/exports", json={})
        _run_job(handlers, r.json()["job_id"])
        e = c.get(f"/api/exports/{r.json()['export_id']}").json()
        assert e["status"] == "ready" and e["search"] is True and built == ["source.osm.pbf"]

        def boom(*a, **k):
            raise RuntimeError("osmium exploded")

        monkeypatch.setattr(handlers.search_mod, "build_index", boom)
        r = c.post(f"/api/maps/{mid}/exports", json={})
        _run_job(handlers, r.json()["job_id"])
        e = c.get(f"/api/exports/{r.json()['export_id']}").json()
        assert e["status"] == "ready" and e["search"] is False
        assert "search index failed: osmium exploded" in c.get(f"/api/exports/{e['id']}/log").json()["log"]


def test_postcode_query_ranks_the_postcode_first_and_poi_twin_is_dropped(tmp_path):
    feats = FEATURES + [pt(19.95, 49.29, name="Zakopane", railway="station", tourism="information")]
    idx = tmp_path / "s.sqlite"
    search.write_index(search.collect(feats), idx)
    for q in ("34-500", "34500"):
        assert search.search(idx, q)[0]["kind"] == "postcode", q
    res = search.search(idx, "zakopane")
    assert res[0]["kind"] == "place"
    assert not [r for r in res if r["kind"] == "poi" and r["label"] == "Zakopane"]
