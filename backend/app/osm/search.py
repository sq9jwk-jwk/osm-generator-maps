"""Offline address / place search: build an SQLite FTS5 index from a ``.osm.pbf`` and query it.

The index lives next to an export (``search.sqlite``) and is made only from the data of that
export's area. It holds five kinds of items: ``place`` (cities, villages...), ``street``,
``postcode``, ``address`` (street + house number) and ``poi`` (named amenities, peaks...).
Text is stored with diacritics folded (``Żółkiewskiego`` -> ``zolkiewskiego``), so the user can
type without Polish letters. Only the stdlib (``sqlite3`` with FTS5) and ``osmium`` are needed.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

INDEX_NAME = "search.sqlite"
SCHEMA_VERSION = "1"

# Extra rows the filter keeps: ``w/highway`` (streets), anything with an address or postcode,
# places, postal boundaries and a few kinds of named points (checked again in Python).
OSMIUM_FILTER = [
    "w/highway",
    "nwr/addr:housenumber",
    "nwr/addr:postcode",
    "nwr/place",
    "nwr/boundary=postal_code",
    "nwr/postal_code",
    "nwr/tourism",
    "nwr/amenity",
    "nwr/natural=peak",
    "nwr/historic",
    "nwr/shop",
]

_PLACE_SKIP = {"house", "plot", "farm"}
_POI_KEYS = ("tourism", "amenity", "historic", "shop")
_KIND_PENALTY = {"place": 0.0, "street": 1.0, "postcode": 1.0, "address": 2.0, "poi": 3.0}
_STREET_LINK_DEG = 0.01  # segments of the same name closer than this (~1 km) are one street
_PLACE_CELL = 0.1

_EXTRA_FOLD = str.maketrans({"ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ß": "ss", "æ": "ae"})
_TOKEN = re.compile(r"[0-9a-z]+")

Cancel = Callable[[], bool]
Log = Callable[[str], None]


def fold(text: str) -> str:
    """Lower-case and strip diacritics (``Żółć`` -> ``zolc``)."""
    t = unicodedata.normalize("NFKD", text.translate(_EXTRA_FOLD))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(fold(text))


# --------------------------------------------------------------------- geometry helpers

def _coords(geom: dict[str, Any]) -> list[tuple[float, float]]:
    """Representative coordinates of a GeoJSON geometry (outer ring / main line)."""
    t, c = geom.get("type"), geom.get("coordinates")
    if not c:
        return []
    if t == "Point":
        return [(c[0], c[1])]
    if t == "LineString":
        return [(p[0], p[1]) for p in c]
    if t == "Polygon":
        return [(p[0], p[1]) for p in c[0]]
    if t == "MultiPolygon":
        return [(p[0], p[1]) for ring in c for p in ring[0]]
    if t == "MultiLineString":
        return [(p[0], p[1]) for line in c for p in line]
    return []


Box = tuple[float, float, float, float]


def _box(pts: list[tuple[float, float]]) -> Box:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _centre(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


class _PlaceIndex:
    """Nearest named place for a point, via a coarse grid."""

    def __init__(self, places: list[tuple[str, float, float]]) -> None:
        self.cells: dict[tuple[int, int], list[tuple[str, float, float]]] = defaultdict(list)
        for p in places:
            self.cells[self._key(p[1], p[2])].append(p)

    @staticmethod
    def _key(lon: float, lat: float) -> tuple[int, int]:
        return (int(lon // _PLACE_CELL), int(lat // _PLACE_CELL))

    def nearest(self, lon: float, lat: float) -> str | None:
        kx, ky = self._key(lon, lat)
        best: tuple[float, str] | None = None
        for ring in (1, 3):  # first the 3x3 neighbourhood, then 7x7
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for name, x, y in self.cells.get((kx + dx, ky + dy), ()):
                        d = (x - lon) ** 2 + (y - lat) ** 2
                        if best is None or d < best[0]:
                            best = (d, name)
            if best:
                break
        return best[1] if best else None


# --------------------------------------------------------------------- reading the PBF

def _run(cmd: list[str], log: Log, cancel: Cancel, what: str) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
        if cancel():
            proc.kill()
            raise InterruptedError("cancelled")
    if proc.wait() != 0:
        raise RuntimeError(f"{what} zakończył się błędem (kod {proc.returncode})")


def _features(pbf: Path, osmium: str, work: Path, log: Log, cancel: Cancel) -> Iterator[dict[str, Any]]:
    filtered = work / "search.tmp.osm.pbf"
    try:
        _run([osmium, "tags-filter", "--overwrite", "-o", str(filtered), str(pbf), *OSMIUM_FILTER],
             log, cancel, "osmium tags-filter")
        proc = subprocess.Popen(
            [osmium, "export", "-f", "geojsonseq", "--overwrite", "-o", "-", str(filtered)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        assert proc.stdout is not None
        try:
            for n, line in enumerate(proc.stdout):
                line = line.strip("\x1e\r\n ")
                if not line:
                    continue
                if n % 5000 == 0 and cancel():
                    raise InterruptedError("cancelled")
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        if proc.returncode not in (0, -9):
            raise RuntimeError(f"osmium export zakończył się błędem (kod {proc.returncode})")
    finally:
        filtered.unlink(missing_ok=True)


Item = tuple[str, str, str, float, float, Box | None, str]  # kind, label, sub, lon, lat, bbox, extra text


def collect(features: Iterable[dict[str, Any]], log: Log = lambda _: None) -> Iterator[Item]:
    """Turn raw OSM features into search items (aggregating streets and postcodes).

    Addresses (the bulk of a country file) are yielded lazily, so they are never all in memory twice.
    """
    places: list[tuple[str, float, float]] = []
    addresses: list[tuple[str | None, str, str | None, str | None, float, float]] = []
    segments: dict[str, list[Box]] = defaultdict(list)
    codes: dict[str, dict[str, Any]] = {}
    items: list[Item] = []

    def add_code(code: str, box: Box, city: str | None) -> None:
        code = code.strip()
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z \-]{1,9}", code):
            return
        e = codes.setdefault(code, {"box": box, "cities": Counter()})
        e["box"] = _union(e["box"], box)
        if city:
            e["cities"][city] += 1

    for f in features:
        p: dict[str, Any] = f.get("properties") or {}
        pts = _coords(f.get("geometry") or {})
        if not pts:
            continue
        box = _box(pts)
        lon, lat = _centre(box)
        name = (p.get("name") or "").strip()
        city = p.get("addr:city") or p.get("addr:place")

        if p.get("highway") and name:
            segments[name].append(box)
        place = p.get("place")
        if place and name and place not in _PLACE_SKIP:
            places.append((name, lon, lat))
            items.append(("place", name, str(place), lon, lat, box if box[0] != box[2] else None, ""))
        if p.get("addr:housenumber") and (p.get("addr:street") or p.get("addr:place")):
            addresses.append((p.get("addr:street") or p.get("addr:place"), str(p["addr:housenumber"]),
                              p.get("addr:postcode"), city, lon, lat))
        code = p.get("addr:postcode") or p.get("postal_code") or (
            p.get("ref") if p.get("boundary") == "postal_code" else None)
        if code:
            add_code(str(code), box, city or (name if place else None))
        if name and not place and (p.get("natural") == "peak" or any(p.get(k) for k in _POI_KEYS)):
            where = ", ".join(x for x in (
                f"{p['addr:street']} {p['addr:housenumber']}".strip() if p.get("addr:street") and p.get("addr:housenumber")
                else p.get("addr:street"), city) if x)
            items.append(("poi", name, where, lon, lat, None, ""))

    pindex = _PlaceIndex(places)

    # addresses: "Street 12, City" (the city is taken from the nearest place when the tag is missing)
    addr_by_street: dict[str, list[tuple[float, float, str | None, str | None, str]]] = defaultdict(list)
    for i, (street, hn, pc, city, lon, lat) in enumerate(addresses):
        assert street is not None
        city = city or pindex.nearest(lon, lat)
        addr_by_street[sys.intern(street)].append((lon, lat, sys.intern(city) if city else None, pc, hn))
        addresses[i] = (None, "", None, None, 0.0, 0.0)  # free the raw tuple
    n_addr = sum(len(v) for v in addr_by_street.values())

    # postcodes
    for code, e in codes.items():
        box = e["box"]
        lon, lat = _centre(box)
        city = e["cities"].most_common(1)[0][0] if e["cities"] else pindex.nearest(lon, lat)
        label = f"{code}" + (f" {city}" if city else "")
        bb = box if (box[2] - box[0]) > 1e-5 or (box[3] - box[1]) > 1e-5 else None
        items.append(("postcode", label, "kod pocztowy", lon, lat, bb, code.replace("-", "")))

    # streets: segments with one name are split into separate streets when far apart
    for name, boxes in segments.items():
        for cluster in _cluster(boxes):
            box = cluster[0]
            for b in cluster[1:]:
                box = _union(box, b)
            lon, lat = _centre(box)
            local = [a for a in addr_by_street.get(name, ())
                     if box[0] - _STREET_LINK_DEG <= a[0] <= box[2] + _STREET_LINK_DEG
                     and box[1] - _STREET_LINK_DEG <= a[1] <= box[3] + _STREET_LINK_DEG]
            cities = Counter(a[2] for a in local if a[2])
            city = cities.most_common(1)[0][0] if cities else pindex.nearest(lon, lat)
            pcs = Counter(a[3] for a in local if a[3])
            pc = pcs.most_common(1)[0][0] if pcs else None
            items.append(("street", name + (f", {city}" if city else ""), pc or "", lon, lat, box,
                          pc.replace("-", "") if pc else ""))
    log(f"search: {len(items)} places/streets/postcodes/POIs + {n_addr} addresses")
    yield from items
    for street, lst in addr_by_street.items():  # the bulk: generated one by one, never stored twice
        for lon, lat, city, pc, hn in lst:
            yield ("address", f"{street} {hn}" + (f", {city}" if city else ""), pc or "", lon, lat, None,
                   pc.replace("-", "") if pc else "")


def _cluster(boxes: list[Box]) -> list[list[Box]]:
    """Group boxes whose centres are within ``_STREET_LINK_DEG`` of each other (union-find on a grid)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = _STREET_LINK_DEG
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, b in enumerate(boxes):
        cx, cy = _centre(b)
        k = (int(cx // cell), int(cy // cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((k[0] + dx, k[1] + dy), ()):
                    jx, jy = _centre(boxes[j])
                    if abs(jx - cx) <= cell and abs(jy - cy) <= cell:
                        parent[find(i)] = find(j)
        grid[k].append(i)
    groups: dict[int, list[Box]] = defaultdict(list)
    for i, b in enumerate(boxes):
        groups[find(i)].append(b)
    return list(groups.values())


# --------------------------------------------------------------------- the index

def write_index(items: Iterable[Item], out: Path) -> int:
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(
            """
            CREATE TABLE items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
                sub TEXT, lon REAL NOT NULL, lat REAL NOT NULL, w REAL, s REAL, e REAL, n REAL);
            CREATE VIRTUAL TABLE fts USING fts5(text, tokenize='unicode61 remove_diacritics 0',
                prefix='2 3');
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        count = 0
        for i, (kind, label, sub, lon, lat, box, extra) in enumerate(items, 1):
            count = i
            b = box or (None, None, None, None)
            con.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (i, kind, label, sub, lon, lat, *b))
            con.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)",
                        (i, fold(f"{label} {sub if kind != 'place' else ''} {extra}")))
        con.execute("INSERT INTO fts(fts) VALUES ('optimize')")
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [("version", SCHEMA_VERSION), ("count", str(count))])
        con.commit()
    finally:
        con.close()
    tmp.replace(out)
    return count


def build_index(
    pbf: Path, out: Path, osmium: str, work: Path, log: Log = lambda _: None,
    cancel: Cancel = lambda: False,
) -> int:
    """Index ``pbf`` into ``out``. Returns the number of items."""
    return write_index(collect(_features(pbf, osmium, work, log, cancel), log), out)


def _match_query(q: str) -> str | None:
    toks = tokens(q)
    if not toks:
        return None
    return " AND ".join(f'"{t}"*' for t in toks[:8])


def search(index: Path, q: str, limit: int = 8) -> list[dict[str, Any]]:
    """Prefix search over the index (every word of ``q`` has to match the start of a word)."""
    match = _match_query(q)
    if match is None or not index.exists():
        return []
    con = sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows: list[sqlite3.Row] = []
    try:
        # per kind, so thousands of addresses sharing a postcode cannot push the postcode out
        for kind in _KIND_PENALTY:
            rows += con.execute(
                "SELECT i.*, bm25(fts) AS score FROM fts JOIN items i ON i.id = fts.rowid"
                " WHERE fts MATCH ? AND i.kind = ? ORDER BY score LIMIT 30",
                (match, kind),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    folded = "".join(tokens(q))  # compared without separators: "34-500" == "34500"
    scored = []
    for r in rows:
        s = r["score"] + _KIND_PENALTY.get(r["kind"], 3.0)
        if "".join(tokens(r["label"])).startswith(folded):
            s -= 4.0
        scored.append((s, r))
    scored.sort(key=lambda t: t[0])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    names = {(r["label"]) for _, r in scored if r["kind"] in ("place", "street")}
    for _, r in scored:
        key = (r["kind"], r["label"])
        if key in seen or (r["kind"] == "poi" and r["label"] in names):  # no twin of a place / street
            continue
        seen.add(key)
        box = [r["w"], r["s"], r["e"], r["n"]] if r["w"] is not None else None
        out.append({"kind": r["kind"], "label": r["label"], "sub": r["sub"] or "",
                    "lon": r["lon"], "lat": r["lat"], "bbox": box})
        if len(out) >= limit:
            break
    return out
