import maplibregl, { type IControl } from "maplibre-gl";
import { api, type BBox, type SearchHit } from "./api";

const KIND_PL: Record<SearchHit["kind"], string> = {
  place: "Miejscowość", street: "Ulica", postcode: "Kod pocztowy", address: "Adres", poi: "Miejsce",
};
const DEBOUNCE_MS = 180;
const MIN_CHARS = 2;

export interface SearchState {
  /** Export id the search runs on (null = nothing selected). */
  exportId: string | null;
  /** True when that export has a search index. */
  indexed: boolean;
  /** True while the index is being built. */
  building: boolean;
}

/** Address / place search box over the map, with suggestions from the export's offline index. */
export class SearchControl implements IControl {
  private map?: maplibregl.Map;
  private root = document.createElement("div");
  private input = document.createElement("input");
  private list = document.createElement("ul");
  private status = document.createElement("div");
  private build = document.createElement("button");
  private marker?: maplibregl.Marker;
  private hits: SearchHit[] = [];
  private active = -1;
  private seq = 0;
  private timer = 0;
  private abort?: AbortController;
  private state: SearchState = { exportId: null, indexed: false, building: false };

  constructor(private onBuild: (exportId: string) => void) {
    this.root.className = "maplibregl-ctrl search-ctrl";
    this.root.id = "search-ctrl";
    this.input.type = "search";
    this.input.id = "search-input";
    this.input.autocomplete = "off";
    this.input.spellcheck = false;
    this.input.setAttribute("role", "combobox");
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.setAttribute("aria-expanded", "false");
    this.input.setAttribute("aria-controls", "search-list");
    this.list.id = "search-list";
    this.list.setAttribute("role", "listbox");
    this.list.hidden = true;
    this.status.className = "search-status";
    this.build.type = "button";
    this.build.id = "search-build";
    this.build.textContent = "Zbuduj indeks wyszukiwania";
    this.build.hidden = true;
    this.build.addEventListener("click", () => {
      if (this.state.exportId) this.onBuild(this.state.exportId);
    });
    this.root.append(this.input, this.list, this.status, this.build);

    this.input.addEventListener("input", () => this.schedule());
    this.input.addEventListener("focus", () => { if (this.hits.length) this.showList(true); });
    this.input.addEventListener("keydown", (e) => this.onKey(e));
    this.list.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in the input
    document.addEventListener("mousedown", (e) => {
      if (!this.root.contains(e.target as Node)) this.showList(false);
    });
    this.render();
  }

  onAdd(map: maplibregl.Map): HTMLElement {
    this.map = map;
    return this.root;
  }

  onRemove(): void {
    this.root.remove();
    this.marker?.remove();
    this.map = undefined;
  }

  /** Call whenever the selected export or its index status changes. */
  setState(s: SearchState): void {
    const changedExport = s.exportId !== this.state.exportId;
    this.state = s;
    if (changedExport) this.clear();
    this.render();
  }

  private render(): void {
    const { exportId, indexed, building } = this.state;
    const usable = !!exportId && indexed;
    this.input.disabled = !usable;
    this.input.placeholder = !exportId
      ? "Wybierz eksport, aby szukać adresów"
      : building ? "Budowanie indeksu wyszukiwania…"
      : indexed ? "Szukaj: ulica, kod pocztowy, miejscowość…"
      : "Ten eksport nie ma indeksu wyszukiwania";
    this.build.hidden = !(exportId && !indexed && !building);
    if (!usable) this.showList(false);
    this.status.hidden = !this.status.textContent;
  }

  private setStatus(text: string): void {
    this.status.textContent = text;
    this.status.hidden = !text;
  }

  private schedule(): void {
    window.clearTimeout(this.timer);
    const q = this.input.value.trim();
    if (q.length < MIN_CHARS) {
      this.abort?.abort();
      this.seq++;
      this.hits = [];
      this.showList(false);
      this.setStatus("");
      return;
    }
    this.timer = window.setTimeout(() => void this.run(q), DEBOUNCE_MS);
  }

  private async run(q: string): Promise<void> {
    const id = this.state.exportId;
    if (!id || !this.state.indexed) return;
    const my = ++this.seq;
    this.abort?.abort();
    this.abort = new AbortController();
    try {
      const hits = await api.search(id, q, 8, this.abort.signal);
      if (my !== this.seq || id !== this.state.exportId) return; // an older answer
      this.hits = hits;
      this.active = -1;
      this.renderList();
      this.setStatus(hits.length ? "" : "Brak wyników");
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      if (my !== this.seq) return;
      this.hits = [];
      this.renderList();
      this.setStatus(`Błąd wyszukiwania: ${(e as Error).message}`);
    }
  }

  private renderList(): void {
    this.list.innerHTML = "";
    this.hits.forEach((h, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.id = `search-opt-${i}`;
      li.dataset.kind = h.kind;
      li.setAttribute("aria-selected", String(i === this.active));
      const label = document.createElement("span");
      label.className = "s-label";
      label.textContent = h.label;
      const kind = document.createElement("span");
      kind.className = "s-kind";
      kind.textContent = KIND_PL[h.kind] + (h.sub && h.kind !== "place" ? ` · ${h.sub}` : "");
      li.append(label, kind);
      li.addEventListener("click", () => this.pick(i));
      this.list.append(li);
    });
    this.showList(this.hits.length > 0);
  }

  private showList(on: boolean): void {
    this.list.hidden = !on;
    this.input.setAttribute("aria-expanded", String(on));
    if (!on) this.input.removeAttribute("aria-activedescendant");
  }

  private move(delta: number): void {
    if (!this.hits.length) return;
    const n = this.hits.length;
    this.active = this.active < 0 && delta < 0 ? n - 1 : (this.active + delta + n) % n;
    [...this.list.children].forEach((li, i) => {
      li.setAttribute("aria-selected", String(i === this.active));
      if (i === this.active) (li as HTMLElement).scrollIntoView({ block: "nearest" });
    });
    this.input.setAttribute("aria-activedescendant", `search-opt-${this.active}`);
  }

  private onKey(e: KeyboardEvent): void {
    if (e.key === "ArrowDown") { e.preventDefault(); if (this.list.hidden) this.showList(this.hits.length > 0); this.move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (this.active >= 0) this.pick(this.active);
      else if (this.hits.length) this.pick(0);
      else {  // Enter before the debounce fired: search right away and open the first hit
        window.clearTimeout(this.timer);
        const q = this.input.value.trim();
        if (q.length >= MIN_CHARS) void this.run(q).then(() => { if (this.hits.length) this.pick(0); });
      }
    } else if (e.key === "Escape") {
      if (!this.list.hidden) this.showList(false);
      else this.clear();
    }
  }

  private pick(i: number): void {
    const h = this.hits[i];
    if (!h || !this.map) return;
    this.input.value = h.label;
    this.showList(false);
    this.setStatus("");
    this.marker?.remove();
    this.marker = new maplibregl.Marker({ color: "#dc2626" }).setLngLat([h.lon, h.lat]).addTo(this.map);
    this.marker.getElement().dataset.testid = "search-marker";
    const zoom = h.kind === "place" ? 13 : 17;
    const area = h.bbox && h.kind !== "address" && h.kind !== "poi" && (h.bbox[2] - h.bbox[0] > 1e-5 || h.bbox[3] - h.bbox[1] > 1e-5);
    if (area) this.map.fitBounds(h.bbox as BBox, { padding: 60, maxZoom: 17, duration: 600 });
    else this.map.flyTo({ center: [h.lon, h.lat], zoom: Math.max(this.map.getZoom(), zoom), duration: 600 });
  }

  /** Empties the box and removes the marker. */
  clear(): void {
    window.clearTimeout(this.timer);
    this.abort?.abort();
    this.seq++;
    this.hits = [];
    this.active = -1;
    this.input.value = "";
    this.list.innerHTML = "";
    this.showList(false);
    this.setStatus("");
    this.marker?.remove();
    this.marker = undefined;
  }
}
