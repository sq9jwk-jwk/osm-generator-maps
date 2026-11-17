import maplibregl, { type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import { api, uploadPbf, type Job, type MapInfo } from "./api";
import { SelectionGrid } from "./grid";
import { estimateBytes, formatBytes, regrid } from "./tiles";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

// Offline: empty style until a map with vector tiles is chosen.
const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9e9e9" } }],
};

const map = new maplibregl.Map({ container: "map", style: EMPTY_STYLE, center: [19.4, 52.0], zoom: 5 });
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({}), "bottom-left");

const grid = new SelectionGrid(map);
let current: MapInfo | null = null;
let maps: MapInfo[] = [];

function toast(msg: string) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 5000);
}

function enable(id: string, on: boolean) { $(id).classList.toggle("disabled", !on); }

// ------------------------------------------------------------------ overlays

function installOverlays() {
  if (!map.getSource("bbox")) map.addSource("bbox", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  if (!map.getLayer("bbox-line"))
    map.addLayer({ id: "bbox-line", type: "line", source: "bbox", paint: { "line-color": "#dc2626", "line-width": 2 } });
  grid.install();
  drawBBox();
}

function drawBBox() {
  const src = map.getSource("bbox") as maplibregl.GeoJSONSource | undefined;
  if (!src) return;
  const b = current?.bbox;
  src.setData(b ? {
    type: "Feature", properties: {},
    geometry: { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] },
  } : { type: "FeatureCollection", features: [] });
}

map.on("style.load", installOverlays);

// ------------------------------------------------------------------ maps

async function refreshMaps() {
  maps = await api.maps();
  const ul = $("maps");
  ul.innerHTML = "";
  for (const m of maps) {
    const li = document.createElement("li");
    if (m.id === current?.id) li.classList.add("active");
    li.innerHTML = `<span class="name" title="${m.message ?? ""}">${m.name}
      <span class="muted">${formatBytes(m.size_bytes)}</span></span>
      <span class="badge ${m.status}">${m.status}</span><button title="Usuń">✕</button>`;
    li.querySelector(".name")!.addEventListener("click", () => selectMap(m.id));
    li.querySelector("button")!.addEventListener("click", async () => {
      if (!confirm(`Usunąć mapę ${m.name}?`)) return;
      try { await api.deleteMap(m.id); if (current?.id === m.id) await selectMap(null); refreshMaps(); }
      catch (e) { toast(String(e)); }
    });
    ul.appendChild(li);
  }
  // A map we are looking at may have just finished importing.
  if (current) {
    const fresh = maps.find((m) => m.id === current!.id);
    if (fresh && fresh.status !== current.status) await selectMap(fresh.id);
  }
}

async function selectMap(id: string | null) {
  current = maps.find((m) => m.id === id) ?? null;
  const ready = current?.status === "ready";
  enable("sec-style", ready);
  enable("sec-output", ready);
  enable("sec-select", ready);
  if (!current) {
    map.setStyle(EMPTY_STYLE);
    grid.setCoverage(null);
    return refreshMaps();
  }
  if (current.bbox) map.fitBounds(current.bbox as [number, number, number, number], { padding: 30, duration: 0 });
  if (ready) {
    applyStyle();
    try { grid.setCoverage(await api.coverage(current.id)); } catch { grid.setCoverage(null); }
  } else {
    map.setStyle(EMPTY_STYLE);
    grid.setCoverage(null);
  }
  drawBBox();
  refreshMaps();
}

function applyStyle() {
  if (!current) return;
  const style = $<HTMLSelectElement>("style").value || "basic";
  map.setStyle(`/api/maps/${current.id}/style/${style}.json`);
}

async function loadStyles() {
  const sel = $<HTMLSelectElement>("style");
  sel.innerHTML = "";
  for (const s of await api.styles()) sel.add(new Option(s.name, s.id));
  sel.onchange = applyStyle;
}

$<HTMLInputElement>("file").addEventListener("change", async (ev) => {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".osm.pbf")) return toast("Wybierz plik .osm.pbf");
  const prog = $("upload-progress");
  try {
    const res = await uploadPbf(file, (p) => (prog.textContent = `Wysyłanie ${file.name}: ${(p * 100).toFixed(1)}%`));
    prog.textContent = "Wysłano. Trwa import (zobacz Zadania).";
    await refreshMaps();
    await selectMap(res.map_id);
  } catch (e) {
    prog.textContent = "";
    toast(`Błąd wysyłania: ${e}`);
  }
});

// ------------------------------------------------------------------ output params & stats

const num = (id: string) => Number($<HTMLInputElement>(id).value);

function outputZooms(): [number, number] {
  const max = num("max-zoom");
  const min = $<HTMLInputElement>("use-range").checked ? Math.min(num("min-zoom"), max) : max;
  return [min, max];
}

function countOutputTiles(): number {
  const [min, max] = outputZooms();
  const n = grid.selected.size;
  let total = 0;
  for (let z = min; z <= max; z++)
    total += z >= grid.gridZ ? n * 4 ** (z - grid.gridZ) : regrid(grid.selected, grid.gridZ, z).size;
  return total;
}

function updateStats() {
  const gz = $<HTMLInputElement>("grid-zoom");
  gz.max = String(num("max-zoom"));
  if (num("grid-zoom") > num("max-zoom")) gz.value = gz.max;
  grid.setGridZoom(num("grid-zoom"));
  $("grid-zoom-val").textContent = String(grid.gridZ);
  $("quality-wrap").hidden = $<HTMLSelectElement>("format").value !== "jpg";
  $<HTMLInputElement>("min-zoom").disabled = !$<HTMLInputElement>("use-range").checked;

  const tiles = countOutputTiles();
  const bytes = estimateBytes(tiles, num("tile-px"), $<HTMLSelectElement>("format").value);
  const [min, max] = outputZooms();
  $("stats").innerHTML =
    `Zaznaczone kwadraty: <b>${grid.selected.size}</b> (siatka z${grid.gridZ})<br>` +
    `Kafelki wyjściowe z${min}${min !== max ? `–z${max}` : ""}: <b>${tiles.toLocaleString("pl-PL")}</b><br>` +
    `Szacowany rozmiar: <b>~${formatBytes(bytes)}</b>` +
    (grid.cellsTooSmall() ? `<br><span class="muted">Przybliż mapę, żeby zobaczyć siatkę.</span>` : "");
  $<HTMLButtonElement>("generate").disabled = tiles === 0;
}

grid.onChange = updateStats;
map.on("moveend", updateStats);
for (const id of ["max-zoom", "min-zoom", "use-range", "tile-px", "format", "grid-zoom", "quality", "scheme"])
  $(id).addEventListener("input", updateStats);
$("sel-all").addEventListener("click", () => grid.selectAllCovered());
$("sel-clear").addEventListener("click", () => grid.clear());

$("generate").addEventListener("click", async () => {
  if (!current) return;
  const [min, max] = outputZooms();
  const tiles = countOutputTiles();
  if (!confirm(`Wygenerować ${tiles.toLocaleString("pl-PL")} kafelków?`)) return;
  try {
    await api.render({
      map_id: current.id,
      style: $<HTMLSelectElement>("style").value,
      grid_zoom: grid.gridZ,
      min_zoom: min,
      max_zoom: max,
      tiles: grid.tiles(),
      tile_px: num("tile-px"),
      format: $<HTMLSelectElement>("format").value,
      quality: num("quality"),
      scheme: $<HTMLSelectElement>("scheme").value,
    });
    toast("Zadanie dodane do kolejki");
    refreshJobs();
  } catch (e) { toast(`Błąd: ${e}`); }
});

// ------------------------------------------------------------------ jobs & outputs

const STATUS_PL: Record<Job["status"], string> = {
  queued: "w kolejce", running: "w toku", done: "gotowe", failed: "błąd", cancelled: "anulowane",
};

async function refreshJobs() {
  const jobs = await api.jobs();
  const ul = $("jobs");
  ul.innerHTML = "";
  for (const j of jobs.slice(0, 15)) {
    const li = document.createElement("li");
    const label = j.kind === "import" ? "Import" : "Render";
    const mapName = maps.find((m) => m.id === j.map_id)?.name ?? "";
    li.innerHTML = `<span>${label} <span class="muted">${mapName}</span><br>
      <span class="muted">${j.message ?? ""}</span></span>
      ${j.status === "running" ? `<progress value="${j.progress}"></progress>` : ""}
      <span class="badge ${j.status}">${STATUS_PL[j.status]}</span>`;
    if (j.status === "running" || j.status === "queued") {
      const b = document.createElement("button");
      b.textContent = "Anuluj";
      b.onclick = () => api.cancelJob(j.id).then(refreshJobs);
      li.appendChild(b);
    }
    ul.appendChild(li);
  }
  if (jobs.some((j) => j.kind === "import")) refreshMaps();
}

async function refreshOutputs() {
  const outs = await api.outputs();
  const ul = $("outputs");
  ul.innerHTML = "";
  for (const o of outs) {
    const li = document.createElement("li");
    const mapName = maps.find((m) => m.id === o.map_id)?.name ?? o.map_id;
    li.innerHTML = `<span>${mapName} · z${o.min_zoom}${o.min_zoom !== o.max_zoom ? `–${o.max_zoom}` : ""}
      · ${o.tile_size}px ${o.format.toUpperCase()} ${o.scheme.toUpperCase()}<br>
      <span class="muted">${o.tiles} kafelków · ${formatBytes(o.size_bytes)} · ${o.created_at}</span></span>
      <a href="/api/outputs/${o.job_id}/zip">ZIP</a><button title="Usuń">✕</button>`;
    li.querySelector("button")!.onclick = async () => {
      if (confirm("Usunąć wynik z dysku?")) { await api.deleteOutput(o.job_id); refreshOutputs(); }
    };
    ul.appendChild(li);
  }
}

// ------------------------------------------------------------------ init

async function init() {
  await loadStyles();
  await refreshMaps();
  await refreshJobs();
  await refreshOutputs();
  updateStats();
  setInterval(refreshJobs, 2000);
  setInterval(refreshOutputs, 10000);
}

init().catch((e) => toast(`Brak połączenia z serwerem: ${e}`));
