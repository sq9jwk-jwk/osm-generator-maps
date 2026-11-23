import type { GeoJSONSource, Map as MlMap, MapMouseEvent } from "maplibre-gl";
import type { Coverage } from "./api";
import { key, lonLatToTile, regrid, tileRing, unkey } from "./tiles";

const MAX_CELLS = 6000;
const SRC = "grid";

/**
 * Selection grid drawn over the map. Cells are exactly the XYZ tiles of
 * the current grid zoom; cells outside the map coverage cannot be selected.
 */
export class SelectionGrid {
  gridZ = 10;
  selected = new Set<string>();
  private coverage: Coverage | null = null;
  private coverageAt = new Map<number, Set<string>>();
  private dragStart: { x: number; y: number; remove: boolean } | null = null;
  private box: HTMLDivElement | null = null;
  onChange: () => void = () => {};
  /** When false the grid is hidden and ignores mouse input (browse-only mode). */
  private active = false;

  /** Set while another map tool (distance measuring) owns the mouse. */
  blocked = false;

  setActive(on: boolean) {
    this.active = on;
    this.dragStart = null;
    this.box?.remove();
    this.box = null;
    this.map.dragPan.enable();
    this.redraw();
  }

  constructor(private map: MlMap) {
    map.boxZoom.disable();
    map.on("moveend", () => this.redraw());
    map.on("click", (e) => this.onClick(e));
    map.on("mousedown", (e) => this.onDown(e));
    map.on("mousemove", (e) => this.onMove(e));
    map.on("mouseup", (e) => this.onUp(e));
  }

  setCoverage(c: Coverage | null) {
    this.coverage = c;
    this.coverageAt.clear();
    this.selected.clear();
    this.redraw();
    this.onChange();
  }

  setGridZoom(z: number) {
    if (z === this.gridZ) return;
    this.selected = regrid(this.selected, this.gridZ, z);
    this.gridZ = z;
    // Drop cells that fall outside coverage after regridding.
    for (const k of [...this.selected]) if (!this.isCovered(...unkey(k))) this.selected.delete(k);
    this.redraw();
    this.onChange();
  }

  clear() { this.selected.clear(); this.redraw(); this.onChange(); }

  selectAllCovered() {
    this.selected = new Set(this.coveredSet(this.gridZ));
    this.redraw();
    this.onChange();
  }

  tiles(): [number, number][] { return [...this.selected].map(unkey); }

  /** Covered cells at zoom z (derived from the coverage mask). */
  private coveredSet(z: number): Set<string> {
    if (!this.coverage) return new Set();
    let s = this.coverageAt.get(z);
    if (!s) {
      const base = new Set(this.coverage.tiles.map(([x, y]) => key(x, y)));
      s = regrid(base, this.coverage.zoom, z);
      this.coverageAt.set(z, s);
    }
    return s;
  }

  isCovered(x: number, y: number): boolean {
    if (!this.coverage) return false;
    const cz = this.coverage.zoom;
    if (this.gridZ > cz) {
      const sh = this.gridZ - cz;
      return this.coveredSet(cz).has(key(x >> sh, y >> sh));
    }
    return this.coveredSet(this.gridZ).has(key(x, y));
  }

  /** (Re)create the grid source and layers; call after every setStyle. */
  install() {
    const m = this.map;
    if (!m.getSource(SRC)) m.addSource(SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    if (!m.getLayer("grid-fill")) {
      m.addLayer({
        id: "grid-fill", type: "fill", source: SRC,
        paint: {
          "fill-color": ["case", ["get", "sel"], "#2563eb", ["get", "cov"], "#ffffff", "#555555"],
          "fill-opacity": ["case", ["get", "sel"], 0.35, ["get", "cov"], 0.0, 0.45],
        },
      });
      m.addLayer({
        id: "grid-line", type: "line", source: SRC,
        paint: { "line-color": ["case", ["get", "sel"], "#1d4ed8", "#888888"], "line-width": 0.6 },
      });
    }
    this.redraw();
  }

  redraw() {
    const src = this.map.getSource(SRC) as GeoJSONSource | undefined;
    if (!src) return;
    const z = this.gridZ;
    const b = this.map.getBounds();
    const [x0, y0] = lonLatToTile(b.getWest(), b.getNorth(), z);
    const [x1, y1] = lonLatToTile(b.getEast(), b.getSouth(), z);
    const n = (x1 - x0 + 1) * (y1 - y0 + 1);
    const features: GeoJSON.Feature[] = [];
    if (this.active && n <= MAX_CELLS) {
      for (let x = x0; x <= x1; x++)
        for (let y = y0; y <= y1; y++)
          features.push({
            type: "Feature",
            properties: { sel: this.selected.has(key(x, y)), cov: this.isCovered(x, y) },
            geometry: { type: "Polygon", coordinates: [tileRing(x, y, z)] },
          });
    }
    src.setData({ type: "FeatureCollection", features });
  }

  private onClick(e: MapMouseEvent) {
    if (!this.active || this.blocked || e.originalEvent.shiftKey || e.originalEvent.altKey || !this.coverage) return;
    const [x, y] = lonLatToTile(e.lngLat.lng, e.lngLat.lat, this.gridZ);
    if (!this.isCovered(x, y)) return;
    const k = key(x, y);
    if (this.selected.has(k)) this.selected.delete(k); else this.selected.add(k);
    this.redraw();
    this.onChange();
  }

  private onDown(e: MapMouseEvent) {
    const ev = e.originalEvent;
    if (!this.active || this.blocked || !(ev.shiftKey || ev.altKey) || !this.coverage) return;
    e.preventDefault();
    this.map.dragPan.disable();
    this.dragStart = { x: e.point.x, y: e.point.y, remove: ev.altKey };
    this.box = document.createElement("div");
    this.box.className = "dragbox" + (ev.altKey ? " remove" : "");
    this.map.getContainer().appendChild(this.box);
  }

  private onMove(e: MapMouseEvent) {
    if (!this.dragStart || !this.box) return;
    const s = this.dragStart;
    Object.assign(this.box.style, {
      left: `${Math.min(s.x, e.point.x)}px`, top: `${Math.min(s.y, e.point.y)}px`,
      width: `${Math.abs(e.point.x - s.x)}px`, height: `${Math.abs(e.point.y - s.y)}px`,
    });
  }

  private onUp(e: MapMouseEvent) {
    if (!this.dragStart) return;
    const s = this.dragStart;
    this.dragStart = null;
    this.box?.remove();
    this.box = null;
    this.map.dragPan.enable();
    const a = this.map.unproject([s.x, s.y]);
    const b = e.lngLat;
    const z = this.gridZ;
    const [xa, ya] = lonLatToTile(Math.min(a.lng, b.lng), Math.max(a.lat, b.lat), z);
    const [xb, yb] = lonLatToTile(Math.max(a.lng, b.lng), Math.min(a.lat, b.lat), z);
    for (let x = xa; x <= xb; x++)
      for (let y = ya; y <= yb; y++) {
        if (!this.isCovered(x, y)) continue;
        if (s.remove) this.selected.delete(key(x, y)); else this.selected.add(key(x, y));
      }
    this.redraw();
    this.onChange();
  }

  cellsTooSmall(): boolean {
    const b = this.map.getBounds();
    const [x0, y0] = lonLatToTile(b.getWest(), b.getNorth(), this.gridZ);
    const [x1, y1] = lonLatToTile(b.getEast(), b.getSouth(), this.gridZ);
    return (x1 - x0 + 1) * (y1 - y0 + 1) > MAX_CELLS;
  }
}
