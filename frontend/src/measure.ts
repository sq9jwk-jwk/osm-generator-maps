import type { GeoJSONSource, IControl, Map as MlMap, MapMouseEvent } from "maplibre-gl";

const SRC = "measure";
const R = 6371008.8; // mean Earth radius, metres

export type LngLat = [number, number];

/** Great-circle distance in metres (haversine). */
export function haversine(a: LngLat, b: LngLat): number {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad;
  const dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Initial bearing a → b in degrees, 0..360. */
export function bearing(a: LngLat, b: LngLat): number {
  const rad = Math.PI / 180;
  const dLon = (b[0] - a[0]) * rad;
  const y = Math.sin(dLon) * Math.cos(b[1] * rad);
  const x = Math.cos(a[1] * rad) * Math.sin(b[1] * rad) -
    Math.sin(a[1] * rad) * Math.cos(b[1] * rad) * Math.cos(dLon);
  return ((Math.atan2(y, x) / rad) + 360) % 360;
}

/** Distance (m) and bearing (deg) of every consecutive pair of points. */
export function segments(p: LngLat[]): { dist: number; az: number }[] {
  const out: { dist: number; az: number }[] = [];
  for (let i = 1; i < p.length; i++) out.push({ dist: haversine(p[i - 1], p[i]), az: bearing(p[i - 1], p[i]) });
  return out;
}

export function formatDistance(m: number): string {
  return m < 1000 ? `${m.toFixed(0)} m` : `${(m / 1000).toFixed(m < 10000 ? 3 : 2)} km`;
}

/**
 * Straight-line distance tool. Click adds a point, right click / Backspace removes
 * the last one, Esc or the ruler button leaves the mode. Works offline (no data needed).
 */
export class MeasureControl implements IControl {
  /** Called when measuring starts/stops so other click handlers (grid) can step aside. */
  onToggle: (active: boolean) => void = () => {};
  private map!: MlMap;
  private box!: HTMLDivElement;
  private btn!: HTMLButtonElement;
  private info!: HTMLDivElement;
  private points: LngLat[] = [];
  private active = false;

  onAdd(map: MlMap): HTMLElement {
    this.map = map;
    this.box = document.createElement("div");
    this.box.className = "maplibregl-ctrl maplibregl-ctrl-group measure-ctrl";
    this.btn = document.createElement("button");
    this.btn.type = "button";
    this.btn.className = "measure-btn";
    this.btn.title = "Pomiar odległości w linii prostej";
    this.btn.textContent = "📏";
    this.btn.addEventListener("click", () => this.setActive(!this.active));
    this.info = document.createElement("div");
    this.info.className = "measure-info";
    this.info.hidden = true;
    this.box.append(this.btn, this.info);

    map.on("style.load", () => this.install());
    if (map.isStyleLoaded()) this.install();
    map.on("click", (e) => this.onClick(e));
    map.on("contextmenu", (e) => this.onContext(e));
    document.addEventListener("keydown", (e) => this.onKey(e));
    return this.box;
  }

  onRemove() { this.box.remove(); }

  setActive(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    this.btn.classList.toggle("on", on);
    this.map.getCanvas().style.cursor = on ? "crosshair" : "";
    if (on) this.map.doubleClickZoom.disable(); else this.map.doubleClickZoom.enable();
    if (!on) this.points = [];
    this.render();
    this.onToggle(on);
  }

  private install() {
    const m = this.map;
    if (!m.getSource(SRC)) m.addSource(SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    if (!m.getLayer("measure-line"))
      m.addLayer({
        id: "measure-line", type: "line", source: SRC, filter: ["==", "$type", "LineString"],
        paint: { "line-color": "#d97706", "line-width": 3 },
      });
    if (!m.getLayer("measure-pts"))
      m.addLayer({
        id: "measure-pts", type: "circle", source: SRC, filter: ["==", "$type", "Point"],
        paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#d97706", "circle-stroke-width": 2 },
      });
    this.render();
  }

  private onClick(e: MapMouseEvent) {
    if (!this.active) return;
    this.points.push([e.lngLat.lng, e.lngLat.lat]);
    this.render();
  }

  private onContext(e: MapMouseEvent) {
    if (!this.active) return;
    e.preventDefault();
    this.points.pop();
    this.render();
  }

  private onKey(e: KeyboardEvent) {
    if (!this.active) return;
    const t = e.target as HTMLElement | null;
    if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
    if (e.key === "Escape") this.setActive(false);
    else if (e.key === "Backspace") { e.preventDefault(); this.points.pop(); this.render(); }
  }

  private render() {
    const src = this.map.getSource(SRC) as GeoJSONSource | undefined;
    const p = this.points;
    if (src) {
      const features: GeoJSON.Feature[] = p.map((c) => ({
        type: "Feature", properties: {}, geometry: { type: "Point", coordinates: c },
      }));
      if (p.length > 1)
        features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: p } });
      src.setData({ type: "FeatureCollection", features });
    }
    this.info.hidden = !this.active;
    if (!this.active) return;
    if (p.length < 2) {
      this.info.textContent = "Klikaj punkty na mapie (PPM/Backspace cofa, Esc kończy)";
      return;
    }
    const segs = segments(p);
    const total = segs.reduce((a, s) => a + s.dist, 0);
    const rows = segs.map((s, i) =>
      `<li>${i + 1}→${i + 2}: ${formatDistance(s.dist)} · ${s.az.toFixed(0)}°</li>`).join("");
    this.info.innerHTML = `Razem: <b>${formatDistance(total)}</b><ol class="measure-segs">${rows}</ol>`;
  }
}
