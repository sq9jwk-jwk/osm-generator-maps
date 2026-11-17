import maplibregl, { type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import {
  api, uploadPbf,
  type BBox, type Job, type MapContents, type MapInfo, type MapPart, type OutputInfo,
  type ProfileInfo, type StyleInfo,
} from "./api";
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
let profiles: ProfileInfo[] = [];
let styles: StyleInfo[] = [];

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
  if (!map.getSource("draft")) map.addSource("draft", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  if (!map.getLayer("draft-fill"))
    map.addLayer({ id: "draft-fill", type: "fill", source: "draft", paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 } });
  if (!map.getLayer("draft-line"))
    map.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#2563eb", "line-width": 2, "line-dasharray": [3, 2] } });
  grid.install();
  drawBBox();
  drawDraft();
}

function bboxFeature(b: BBox | null | undefined): GeoJSON.Feature | GeoJSON.FeatureCollection {
  return b ? {
    type: "Feature", properties: {},
    geometry: { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] },
  } : { type: "FeatureCollection", features: [] };
}

let draftBBox: BBox | null = null;

function setDraft(b: BBox | null) { draftBBox = b; drawDraft(); }

function drawDraft() {
  (map.getSource("draft") as maplibregl.GeoJSONSource | undefined)?.setData(bboxFeature(draftBBox));
}

function drawBBox() {
  const src = map.getSource("bbox") as maplibregl.GeoJSONSource | undefined;
  if (!src) return;
  src.setData(bboxFeature(current?.bbox));
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
  if (library.open) renderLibrary();
  // A map we are looking at may have just finished importing.
  if (current) {
    const fresh = maps.find((m) => m.id === current!.id);
    if (fresh && fresh.status !== current.status) await selectMap(fresh.id);
  }
}

async function selectMap(id: string | null) {
  current = maps.find((m) => m.id === id) ?? null;
  populateStyles(current?.profile ?? "classic");
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

/** Only styles whose required layers the map's import profile produces. */
function populateStyles(profile: string) {
  const sel = $<HTMLSelectElement>("style");
  const keep = sel.value;
  sel.innerHTML = "";
  for (const s of styles)
    if (!s.profiles.length || s.profiles.includes(profile)) sel.add(new Option(s.name, s.id));
  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
}

async function loadStyles() {
  styles = await api.styles();
  populateStyles("classic");
  // Labelled classic style is the default view when present.
  const sel = $<HTMLSelectElement>("style");
  if ([...sel.options].some((o) => o.value === "classic")) sel.value = "classic";
  $<HTMLSelectElement>("style").onchange = applyStyle;
}

// ------------------------------------------------------------------ library dialog

const library = $<HTMLDialogElement>("library");

function td(text: string | Node, cls?: string): HTMLTableCellElement {
  const c = document.createElement("td");
  if (cls) c.className = cls;
  c.append(text);
  return c;
}

function actionButton(label: string, onClick: () => void, danger = false): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  if (danger) b.classList.add("danger");
  b.onclick = onClick;
  return b;
}

async function deleteMapFlow(m: MapInfo, importOnly: boolean) {
  let withOutputs = false;
  if (importOnly) {
    if (!confirm(`Usunąć dane importu mapy „${m.name}” (${formatBytes(m.import_bytes)})?\n` +
      `Plik PBF zostanie zachowany, import można uruchomić ponownie.`)) return;
  } else {
    if (!confirm(`Usunąć mapę „${m.name}” razem z plikiem PBF (${formatBytes(m.source_bytes)}) ` +
      `i danymi importu (${formatBytes(m.import_bytes)})?`)) return;
    if (m.outputs > 0)
      withOutputs = confirm(`Mapa ma ${m.outputs} wygenerowanych wyników. ` +
        `OK = usuń je też, Anuluj = zostaw wyniki na dysku.`);
  }
  try {
    const r = await api.deleteMap(m.id, { importOnly, outputs: withOutputs });
    toast(`Zwolniono ${formatBytes(r.freed_bytes)}`);
    if (current?.id === m.id) await selectMap(null);
    await refreshMaps();
    await refreshOutputs();
    renderLibrary();
  } catch (e) { toast(String(e)); }
}

const expanded = new Set<string>();
const contents = new Map<string, MapContents>();

async function loadContents(id: string) {
  try { contents.set(id, await api.contents(id)); } catch (e) { toast(String(e)); }
  if (library.open) renderLibrary();
}

async function afterDelete(m: MapInfo, freed: number) {
  toast(`Zwolniono ${formatBytes(freed)}`);
  await refreshMaps();
  await refreshOutputs();
  if (expanded.has(m.id)) await loadContents(m.id);
  renderLibrary();
}

async function deletePartFlow(m: MapInfo, part: MapPart, label: string, bytes: number) {
  const extra = part === "source"
    ? "\nBez pliku źródłowego nie da się zaimportować mapy ponownie (podgląd i generowanie nadal działają)."
    : part === "import"
      ? "\nPlik PBF zostanie zachowany, import można uruchomić ponownie. Podgląd przestanie działać do czasu importu."
      : "";
  if (!confirm(`Usunąć: ${label} (${formatBytes(bytes)})?${extra}`)) return;
  try {
    const r = await api.deletePart(m.id, part);
    if (part === "import" && current?.id === m.id) await selectMap(null);
    await afterDelete(m, r.freed_bytes);
  } catch (e) { toast(String(e)); }
}

async function deleteOutputFlow(m: MapInfo, o: OutputInfo) {
  if (!confirm(`Usunąć wynik z${o.min_zoom}–${o.max_zoom} (${formatBytes(o.size_bytes)}) z dysku?`)) return;
  try {
    await api.deleteOutput(o.job_id);
    await afterDelete(m, o.size_bytes);
  } catch (e) { toast(String(e)); }
}

function detailRow(m: MapInfo): HTMLTableRowElement {
  const tr = document.createElement("tr");
  tr.className = "detail";
  const cell = document.createElement("td");
  cell.colSpan = 9;
  const c = contents.get(m.id);
  if (!c) {
    cell.textContent = "Wczytywanie…";
    tr.append(cell);
    return tr;
  }
  const busy = m.status === "importing";
  const ul = document.createElement("ul");
  ul.className = "contents";
  const item = (text: string, bytes: number, ...btns: HTMLButtonElement[]) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = text;
    const size = document.createElement("b");
    size.textContent = formatBytes(bytes);
    li.append(label, size, ...btns);
    ul.append(li);
  };
  for (const p of c.parts) {
    const btns: HTMLButtonElement[] = [];
    if (p.key === "log")
      btns.push(actionButton("Pokaż", async () => {
        const pre = $("library-log");
        pre.textContent = (await api.importLog(m.id)).log || "(pusty log)";
        pre.hidden = false;
      }));
    if (!busy) btns.push(actionButton("Usuń", () => deletePartFlow(m, p.key, p.label, p.bytes), true));
    item(p.label, p.bytes, ...btns);
  }
  for (const o of c.outputs)
    item(
      `Wynik: ${o.style} · z${o.min_zoom}${o.min_zoom !== o.max_zoom ? `–${o.max_zoom}` : ""} · ` +
        `${o.tile_size}px ${o.format.toUpperCase()} ${o.scheme.toUpperCase()} · ` +
        `${o.tiles.toLocaleString("pl-PL")} kafelków`,
      o.size_bytes,
      actionButton("Usuń", () => deleteOutputFlow(m, o), true),
    );
  if (!c.parts.length && !c.outputs.length) {
    const li = document.createElement("li");
    li.textContent = "Brak plików.";
    ul.append(li);
  }
  const total = c.parts.reduce((a, p) => a + p.bytes, 0) + c.outputs.reduce((a, o) => a + o.size_bytes, 0);
  const sum = document.createElement("p");
  sum.className = "muted";
  sum.textContent = `Razem: ${formatBytes(total)}`;
  cell.append(ul, sum);
  tr.append(cell);
  return tr;
}

function renderLibrary() {
  const body = $("library-table").querySelector("tbody")!;
  body.innerHTML = "";
  let total = 0;
  for (const m of maps) {
    total += m.source_bytes + m.import_bytes;
    const tr = document.createElement("tr");
    if (m.id === current?.id) tr.classList.add("active");
    const badge = document.createElement("span");
    badge.className = `badge ${m.status}`;
    badge.textContent = m.status;
    badge.title = m.message ?? "";
    const areaCell = td(m.bbox ? m.bbox.map((v) => v.toFixed(2)).join("  ") : "–");
    const profileName = profiles.find((p) => p.id === m.profile)?.name ?? m.profile;
    const note = document.createElement("div");
    note.className = "muted";
    note.textContent = `profil: ${profileName}${m.region ? " · wycinek" : ""}`;
    areaCell.append(note);
    const open = expanded.has(m.id);
    const toggle = actionButton(open ? "▾" : "▸", () => {
      if (expanded.has(m.id)) expanded.delete(m.id);
      else { expanded.add(m.id); void loadContents(m.id); }
      renderLibrary();
    });
    toggle.title = "Pokaż zawartość i zajęte miejsce";
    const actions = document.createElement("td");
    actions.className = "actions";
    const busy = m.status === "importing";
    actions.append(
      actionButton("Wybierz", async () => { await selectMap(m.id); library.close(); }),
    );
    if (!busy) {
      if (m.source_bytes > 0)
        actions.append(
          actionButton(m.status === "ready" ? "Importuj inaczej…" : "Importuj…", () => void openImportDialog(m)),
        );
      actions.append(actionButton("Usuń mapę", () => deleteMapFlow(m, false), true));
    }
    tr.append(
      td(toggle),
      td(m.name),
      td(new Date(m.created_at * 1000).toLocaleString("pl-PL")),
      areaCell,
      td(badge),
      td(formatBytes(m.source_bytes)),
      td(formatBytes(m.import_bytes)),
      td(String(m.outputs)),
      actions,
    );
    body.appendChild(tr);
    if (open) body.appendChild(detailRow(m));
  }
  $("library-total").textContent = `Map: ${maps.length} · zajęte miejsce (pliki map): ${formatBytes(total)}`;
}

$("open-library").addEventListener("click", async () => {
  await refreshMaps();
  $("library-log").hidden = true;
  renderLibrary();
  library.showModal();
});

// ------------------------------------------------------------------ import dialog, area & sample

const importDlg = $<HTMLDialogElement>("import-dlg");
let importTarget: MapInfo | null = null;
let importRect: BBox | null = null;
let sampleMode: { mapId: string; profile: string } | null = null;

const importProfile = () => $<HTMLSelectElement>("import-profile").value;
const areaMode = () =>
  (document.querySelector<HTMLInputElement>('input[name="import-area"]:checked')?.value ?? "all");
const setArea = (v: "all" | "rect") =>
  (document.querySelector<HTMLInputElement>(`input[name="import-area"][value="${v}"]`)!.checked = true);

function updateImportUi() {
  $("import-profile-desc").textContent = profiles.find((p) => p.id === importProfile())?.description ?? "";
  $("import-bbox").textContent = importRect ? importRect.map((v) => v.toFixed(3)).join("  ") : "";
}

async function openImportDialog(m: MapInfo) {
  importTarget = m;
  importRect = m.region;
  if (!profiles.length) {
    try { profiles = await api.profiles(); } catch (e) { return toast(String(e)); }
  }
  const sel = $<HTMLSelectElement>("import-profile");
  sel.innerHTML = "";
  for (const p of profiles) sel.add(new Option(p.name, p.id));
  sel.value = profiles.some((p) => p.id === m.profile) ? m.profile : profiles[0]?.id ?? "";
  setArea(importRect ? "rect" : "all");
  $("import-title").textContent = `Import: ${m.name}`;
  updateImportUi();
  library.close();
  importDlg.showModal();
}

function rectOf(a: maplibregl.LngLat, b: maplibregl.LngLat): BBox {
  return [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
}

/** Let the user drag a rectangle on the map. Resolves to null when cancelled (Esc) or too small. */
function drawRect(): Promise<BBox | null> {
  return new Promise((resolve) => {
    let start: maplibregl.LngLat | null = null;
    const canvas = map.getCanvas();
    map.dragPan.disable();
    canvas.style.cursor = "crosshair";
    toast("Przeciągnij na mapie prostokąt obszaru (Esc anuluje)");
    const swallowClick = (ev: Event) => ev.stopPropagation();
    const done = (b: BBox | null) => {
      map.off("mousedown", onDown);
      map.off("mousemove", onMove);
      map.off("mouseup", onUp);
      document.removeEventListener("keydown", onKey);
      map.dragPan.enable();
      canvas.style.cursor = "";
      // The click that ends the drag must not toggle a selection square.
      window.addEventListener("click", swallowClick, { capture: true });
      setTimeout(() => window.removeEventListener("click", swallowClick, { capture: true }), 100);
      resolve(b);
    };
    const onDown = (e: maplibregl.MapMouseEvent) => { start = e.lngLat; };
    const onMove = (e: maplibregl.MapMouseEvent) => { if (start) setDraft(rectOf(start, e.lngLat)); };
    const onUp = (e: maplibregl.MapMouseEvent) => {
      if (!start) return;
      const b = rectOf(start, e.lngLat);
      done(b[2] - b[0] > 1e-3 && b[3] - b[1] > 1e-3 ? b : null);
    };
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") done(null); };
    map.on("mousedown", onDown);
    map.on("mousemove", onMove);
    map.on("mouseup", onUp);
    document.addEventListener("keydown", onKey);
  });
}

async function chooseRect() {
  importDlg.close();
  const b = await drawRect();
  if (b) importRect = b;
  setDraft(importRect);
  setArea(importRect ? "rect" : "all");
  updateImportUi();
  importDlg.showModal();
}

$("import-draw").addEventListener("click", () => void chooseRect());
document.querySelectorAll<HTMLInputElement>('input[name="import-area"]').forEach((r) =>
  r.addEventListener("change", () => {
    if (areaMode() === "rect" && !importRect) void chooseRect();
    setDraft(areaMode() === "rect" ? importRect : null);
    updateImportUi();
  }));
$("import-profile").addEventListener("change", updateImportUi);
importDlg.addEventListener("close", () => { if (!sampleMode) setDraft(null); });

$("import-start").addEventListener("click", async () => {
  const m = importTarget;
  if (!m) return;
  const rect = areaMode() === "rect";
  const bbox = rect ? importRect : null;
  if (rect && !bbox) return toast("Narysuj prostokąt albo wybierz „cały plik”");
  if (!rect && importProfile() !== "classic" && m.source_bytes > 300 * 1024 ** 2 &&
      !confirm(`Plik ma ${formatBytes(m.source_bytes)}. Ten profil dla całego pliku może trwać bardzo długo ` +
        `i zużyć dużo pamięci. Czy na pewno nie ograniczyć obszaru?\nOK = importuj cały plik mimo to.`)) return;
  if (m.status === "ready" &&
      !confirm("Import zastąpi obecne kafelki wektorowe tej mapy. Wygenerowane wyniki zostaną nietknięte. Kontynuować?")) return;
  try {
    await api.importMap(m.id, importProfile(), bbox);
    importDlg.close();
    toast("Import dodany do kolejki");
    await refreshMaps();
    void refreshJobs();
  } catch (e) { toast(String(e)); }
});

// --- sample

function sampleStyleId(profile: string): string {
  const own = styles.find((s) => s.profiles.includes(profile));
  return (own ?? styles.find((s) => !s.profiles.length) ?? styles[0])?.id ?? "basic";
}

function sampleBBox(m: MapInfo): BBox {
  if (areaMode() === "rect" && importRect) return importRect;
  const v = map.getBounds();
  const view: BBox = [v.getWest(), v.getSouth(), v.getEast(), v.getNorth()];
  const cx = (view[0] + view[2]) / 2;
  const cy = (view[1] + view[3]) / 2;
  const b = m.bbox;
  // A view centred outside the data would give an empty sample; fall back to the file's centre.
  if (b && (cx < b[0] || cx > b[2] || cy < b[1] || cy > b[3])) {
    const mx = (b[0] + b[2]) / 2;
    const my = (b[1] + b[3]) / 2;
    return [mx - 0.05, my - 0.05, mx + 0.05, my + 0.05];
  }
  return view;
}

async function waitForJob(jobId: string): Promise<Job> {
  for (;;) {
    const j = (await api.jobs()).find((x) => x.id === jobId);
    if (j && j.status !== "queued" && j.status !== "running") return j;
    await new Promise((r) => setTimeout(r, 1500));
  }
}

$("import-sample").addEventListener("click", async () => {
  const m = importTarget;
  if (!m) return;
  const btn = $<HTMLButtonElement>("import-sample");
  const profile = importProfile();
  btn.disabled = true;
  btn.textContent = "Generowanie próbki…";
  try {
    await api.deleteSample(m.id);
    const { job_id, bbox } = await api.createSample(m.id, profile, sampleBBox(m));
    void refreshJobs();
    const job = await waitForJob(job_id);
    if (job.status !== "done") throw new Error(job.message ?? "Nie udało się wygenerować próbki");
    sampleMode = { mapId: m.id, profile };
    importDlg.close();
    map.setStyle(`/api/maps/${m.id}/style/${sampleStyleId(profile)}.json?variant=sample`);
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 30, duration: 0 });
    const name = profiles.find((p) => p.id === profile)?.name ?? profile;
    $("sample-text").textContent = `Próbka (ok. 10×10 km), profil „${name}”. Tak będzie wyglądał import.`;
    $("sample-bar").hidden = false;
  } catch (e) {
    toast(`Próbka: ${e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Pokaż próbkę na mapie";
  }
});

async function exitSample() {
  const sm = sampleMode;
  sampleMode = null;
  $("sample-bar").hidden = true;
  setDraft(null);
  if (sm) void api.deleteSample(sm.mapId).catch(() => undefined);
  await selectMap(current?.id ?? null);
}

$("sample-close").addEventListener("click", () => void exitSample());
$("sample-import").addEventListener("click", async () => {
  await exitSample();
  if (importTarget) importDlg.showModal();
});

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
    const label = { import: "Import", render: "Render", sample: "Próbka" }[j.kind];
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
  profiles = await api.profiles().catch(() => []);
  await refreshMaps();
  await refreshJobs();
  await refreshOutputs();
  updateStats();
  setInterval(refreshJobs, 2000);
  setInterval(refreshOutputs, 10000);
}

init().catch((e) => toast(`Brak połączenia z serwerem: ${e}`));
