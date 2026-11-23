import maplibregl, { type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import {
  api, uploadPbf,
  type BBox, type ExportInfo, type Job, type MapContents, type MapInfo, type OutputInfo,
  type ModuleInfo, type ProfileInfo, type StyleInfo,
} from "./api";
import { SelectionGrid } from "./grid";
import { MeasureControl } from "./measure";
import { SearchControl } from "./search";
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

const searchCtl = new SearchControl((id) => {
  api.buildSearch(id).then(() => { toast("Budowanie indeksu wyszukiwania dodane do kolejki"); void refreshJobs(); })
    .catch((e) => toast(String(e)));
});
map.addControl(searchCtl, "top-left");
const grid = new SelectionGrid(map);
const measure = new MeasureControl();
measure.onToggle = (on) => { grid.blocked = on; };
map.addControl(measure, "top-right");
let current: ExportInfo | null = null;
let maps: MapInfo[] = [];
let profiles: ProfileInfo[] = [];
let moduleCatalogue: ModuleInfo[] = [];
let styles: StyleInfo[] = [];

function toast(msg: string) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 5000);
}

function enable(id: string, on: boolean) { $(id).classList.toggle("disabled", !on); }

/** Browse-only by default; the checkbox turns on output parameters and tile selection. */
function applyGenerateMode() {
  const on = $<HTMLInputElement>("gen-enable").checked;
  $("sec-output").hidden = !on;
  $("sec-select").hidden = !on;
  grid.setActive(on);
}
$("gen-enable").addEventListener("change", applyGenerateMode);

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

let draftRects: BBox[] = [];

function setDraft(b: BBox[] | BBox | null) {
  draftRects = b ? (typeof b[0] === "number" ? [b as BBox] : (b as BBox[])) : [];
  drawDraft();
}

function drawDraft() {
  (map.getSource("draft") as maplibregl.GeoJSONSource | undefined)?.setData({
    type: "FeatureCollection",
    features: draftRects.map((b) => bboxFeature(b) as GeoJSON.Feature),
  });
}

function drawBBox() {
  const src = map.getSource("bbox") as maplibregl.GeoJSONSource | undefined;
  if (!src) return;
  src.setData(bboxFeature(current?.region ?? current?.bbox));
}

map.on("style.load", installOverlays);

// ------------------------------------------------------------------ sources & exports

const exportsOf = (): ExportInfo[] => maps.flatMap((m) => m.exports);
const sourceOf = (e: ExportInfo) => maps.find((m) => m.id === e.map_id);
const exportLabel = (e: ExportInfo) => `${sourceOf(e)?.name ?? "?"} → ${e.name}`;
const STATUS_EXPORT: Record<ExportInfo["status"], string> = {
  importing: "import…", ready: "gotowy", failed: "błąd",
};

async function refreshMaps() {
  maps = await api.maps();
  const ul = $("maps");
  ul.innerHTML = "";
  for (const m of maps) {
    const li = document.createElement("li");
    li.className = "source";
    const head = document.createElement("div");
    head.className = "src-head";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = m.name;
    name.title = m.filename;
    const size = document.createElement("span");
    size.className = "muted";
    size.textContent = formatBytes(m.size_bytes);
    const add = actionButton("+ Eksport", () => void openImportDialog(m));
    add.title = "Nowy eksport z tego pliku";
    add.disabled = m.source_bytes === 0;
    head.append(name, size, add);
    li.append(head);

    const sub = document.createElement("ul");
    sub.className = "exports";
    for (const e of m.exports) {
      const row = document.createElement("li");
      if (e.id === current?.id) row.classList.add("active");
      const label = document.createElement("span");
      label.className = "name";
      label.textContent = e.name;
      label.title = e.message ?? "";
      label.addEventListener("click", () => void selectExport(e.id));
      const badge = document.createElement("span");
      badge.className = `badge ${e.status}`;
      badge.textContent = STATUS_EXPORT[e.status];
      badge.title = e.message ?? "";
      const del = actionButton("✕", () => void deleteExportFlow(m, e), true);
      del.title = "Usuń eksport";
      del.disabled = e.status === "importing";
      row.append(label, badge, del);
      sub.append(row);
    }
    if (!m.exports.length) {
      const empty = document.createElement("li");
      empty.className = "muted";
      empty.textContent = "Brak eksportów. Utwórz pierwszy przyciskiem „+ Eksport”.";
      sub.append(empty);
    }
    li.append(sub);
    ul.append(li);
  }
  if (library.open) renderLibrary();
  updateSearch();
  // The export we are looking at may have just finished (or been removed).
  if (current) {
    const fresh = exportsOf().find((e) => e.id === current!.id);
    if (!fresh) await selectExport(null);
    else if (fresh.status !== current.status) await selectExport(fresh.id);
  }
}

/** Which export the style dropdown was last chosen for (a new export deserves a fresh default). */
let styleFor = "";

/** Best default view for an export: a labelled style, tourist when it has trails. */
function defaultStyleFor(e: ExportInfo): string {
  const has = (id: string) => [...$<HTMLSelectElement>("style").options].some((o) => o.value === id);
  const hiking = e.profile === "tourist" || (e.profile === "custom" && !!e.modules?.includes("hiking"));
  if (e.profile === "full" && has("bright")) return "bright";
  if (hiking && has("tourist")) return "tourist";
  return has("classic") ? "classic" : "basic";
}

/** Keeps the search box in step with the selected export (its index may have just been built). */
function updateSearch() {
  const fresh = current ? exportsOf().find((e) => e.id === current!.id) : undefined;
  const ready = fresh?.status === "ready";
  searchCtl.setState({
    exportId: ready ? fresh!.id : null,
    indexed: !!fresh?.search,
    building: !!fresh && lastJobs.some((j) => j.kind === "geocode" && j.export_id === fresh.id
      && (j.status === "queued" || j.status === "running")),
  });
}

let lastJobs: Job[] = [];

async function selectExport(id: string | null) {
  current = exportsOf().find((e) => e.id === id) ?? null;
  updateSearch();
  populateStyles(current?.profile ?? "classic");
  if (current?.status === "ready") {
    const sig = `${current.id}|${current.bytes}`;
    if (sig !== styleFor) {
      styleFor = sig;
      $<HTMLSelectElement>("style").value = defaultStyleFor(current);
    }
  }
  const ready = current?.status === "ready";
  enable("sec-style", ready);
  enable("sec-output", ready);
  enable("sec-select", ready);
  enable("sec-generate", ready);
  if (!ready) { $<HTMLInputElement>("gen-enable").checked = false; applyGenerateMode(); }
  if (!current) {
    map.setStyle(EMPTY_STYLE);
    grid.setCoverage(null);
    return refreshMaps();
  }
  const fit = current.region ?? current.bbox;
  if (fit) map.fitBounds(fit as [number, number, number, number], { padding: 30, duration: 0 });
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
  map.setStyle(`/api/exports/${current.id}/style/${style}.json`);
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

async function deleteMapFlow(m: MapInfo) {
  const n = m.exports.length;
  if (!confirm(`Usunąć mapę „${m.name}” razem z plikiem PBF (${formatBytes(m.source_bytes)}) ` +
    `i wszystkimi eksportami (${n}, ${formatBytes(m.import_bytes)})?`)) return;
  let withOutputs = false;
  if (m.outputs > 0)
    withOutputs = confirm(`Mapa ma ${m.outputs} wygenerowanych wyników. ` +
      `OK = usuń je też, Anuluj = zostaw wyniki na dysku.`);
  try {
    const r = await api.deleteMap(m.id, { outputs: withOutputs });
    toast(`Zwolniono ${formatBytes(r.freed_bytes)}`);
    if (current?.map_id === m.id) await selectExport(null);
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

async function deleteSourceFlow(m: MapInfo, bytes: number) {
  if (!confirm(`Usunąć plik źródłowy .osm.pbf mapy „${m.name}” (${formatBytes(bytes)})?\n` +
    `Eksporty i ich podgląd zostają, ale nie da się utworzyć nowych eksportów z tego pliku.`)) return;
  try {
    const r = await api.deleteSource(m.id);
    await afterDelete(m, r.freed_bytes);
  } catch (e) { toast(String(e)); }
}

async function deleteExportFlow(m: MapInfo, e: ExportInfo) {
  if (!confirm(`Usunąć eksport „${e.name}” (${formatBytes(e.bytes)})?\n` +
    `Plik PBF i pozostałe eksporty zostają.`)) return;
  let withOutputs = false;
  if (e.outputs > 0)
    withOutputs = confirm(`Eksport ma ${e.outputs} wygenerowanych wyników. ` +
      `OK = usuń je też, Anuluj = zostaw wyniki na dysku.`);
  try {
    const r = await api.deleteExport(e.id, withOutputs);
    if (current?.id === e.id) await selectExport(null);
    await afterDelete(m, r.freed_bytes);
  } catch (err) { toast(String(err)); }
}

async function deleteOutputFlow(m: MapInfo, o: OutputInfo) {
  if (!confirm(`Usunąć wynik z${o.min_zoom}–${o.max_zoom} (${formatBytes(o.size_bytes)}) z dysku?`)) return;
  try {
    await api.deleteOutput(o.job_id);
    await afterDelete(m, o.size_bytes);
  } catch (e) { toast(String(e)); }
}

function profileLabel(e: ExportInfo): string {
  const base = profiles.find((p) => p.id === e.profile)?.name ?? e.profile;
  return e.profile === "custom" && e.modules ? `${base}: ${e.modules.length} elem.` : base;
}

function outputLine(o: OutputInfo): string {
  return `Wynik: ${o.style} · z${o.min_zoom}${o.min_zoom !== o.max_zoom ? `–${o.max_zoom}` : ""} · ` +
    `${o.tile_size}px ${o.format.toUpperCase()} ${o.scheme.toUpperCase()} · ` +
    `${o.tiles.toLocaleString("pl-PL")} kafelków`;
}

function detailRow(m: MapInfo): HTMLTableRowElement {
  const tr = document.createElement("tr");
  tr.className = "detail";
  const cell = document.createElement("td");
  cell.colSpan = 8;
  const c = contents.get(m.id);
  if (!c) {
    cell.textContent = "Wczytywanie…";
    tr.append(cell);
    return tr;
  }
  const ul = document.createElement("ul");
  ul.className = "contents";
  const item = (text: string, bytes: number, cls: string, ...btns: HTMLButtonElement[]) => {
    const li = document.createElement("li");
    if (cls) li.className = cls;
    const label = document.createElement("span");
    label.textContent = text;
    const size = document.createElement("b");
    size.textContent = formatBytes(bytes);
    li.append(label, size, ...btns);
    ul.append(li);
  };
  if (c.source_bytes > 0)
    item("Plik źródłowy .osm.pbf", c.source_bytes, "",
      actionButton("Usuń", () => deleteSourceFlow(m, c.source_bytes), true));
  const known = new Set(c.exports.map((e) => e.id));
  for (const e of c.exports) {
    const btns: HTMLButtonElement[] = [];
    if (e.status === "ready")
      btns.push(actionButton("Otwórz", async () => { await selectExport(e.id); library.close(); }));
    btns.push(actionButton("Log", async () => {
      const pre = $("library-log");
      pre.textContent = (await api.exportLog(e.id)).log || "(pusty log)";
      pre.hidden = false;
    }));
    if (e.status !== "importing") btns.push(actionButton("Usuń", () => deleteExportFlow(m, e), true));
    item(
      `Eksport „${e.name}”: ${profileLabel(e)}${e.region ? " · wycinek" : " · cały plik"} · ` +
        `${STATUS_EXPORT[e.status]} · ${new Date(e.created_at * 1000).toLocaleString("pl-PL")}`,
      e.bytes, "", ...btns);
    for (const o of c.outputs.filter((x) => x.export_id === e.id))
      item(outputLine(o), o.size_bytes, "sub", actionButton("Usuń", () => deleteOutputFlow(m, o), true));
  }
  for (const o of c.outputs.filter((x) => !x.export_id || !known.has(x.export_id)))
    item(`${outputLine(o)} (bez eksportu)`, o.size_bytes, "", actionButton("Usuń", () => deleteOutputFlow(m, o), true));
  if (!c.source_bytes && !c.exports.length && !c.outputs.length) {
    const li = document.createElement("li");
    li.textContent = "Brak plików.";
    ul.append(li);
  }
  const total = c.source_bytes + c.exports.reduce((a, e) => a + e.bytes, 0) +
    c.outputs.reduce((a, o) => a + o.size_bytes, 0);
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
    if (m.exports.some((e) => e.id === current?.id)) tr.classList.add("active");
    const open = expanded.has(m.id);
    const toggle = actionButton(open ? "▾" : "▸", () => {
      if (expanded.has(m.id)) expanded.delete(m.id);
      else { expanded.add(m.id); void loadContents(m.id); }
      renderLibrary();
    });
    toggle.title = "Pokaż eksporty, wyniki i zajęte miejsce";
    const actions = document.createElement("td");
    actions.className = "actions";
    if (m.source_bytes > 0)
      actions.append(actionButton("Nowy eksport…", () => void openImportDialog(m)));
    if (!m.exports.some((e) => e.status === "importing"))
      actions.append(actionButton("Usuń mapę", () => deleteMapFlow(m), true));
    const exportsCell = td(`${m.exports.length} · ${formatBytes(m.import_bytes)}`);
    tr.append(
      td(toggle),
      td(m.name),
      td(new Date(m.created_at * 1000).toLocaleString("pl-PL")),
      td(m.bbox ? m.bbox.map((v) => v.toFixed(2)).join("  ") : "–"),
      td(formatBytes(m.source_bytes)),
      exportsCell,
      td(String(m.outputs)),
      actions,
    );
    body.appendChild(tr);
    if (open) body.appendChild(detailRow(m));
  }
  $("library-total").textContent =
    `Map: ${maps.length} · eksportów: ${exportsOf().length} · zajęte miejsce (pliki map): ${formatBytes(total)}`;
}

$("open-library").addEventListener("click", async () => {
  await refreshMaps();
  $("library-log").hidden = true;
  renderLibrary();
  library.showModal();
});

// ------------------------------------------------------------------ new export dialog, area & sample

const importDlg = $<HTMLDialogElement>("import-dlg");
let importTarget: MapInfo | null = null;
let importRects: BBox[] = [];
let sampleMode: { mapId: string; profile: string } | null = null;

const importProfile = () => $<HTMLSelectElement>("import-profile").value;
/** Modules ticked in the dialog; null unless the "custom" profile is selected. */
const importModules = (): string[] | null => importProfile() !== "custom" ? null :
  [...document.querySelectorAll<HTMLInputElement>("#import-modules input:checked")].map((i) => i.value);

function renderModules(checked: string[] | null) {
  const box = $("import-modules");
  box.innerHTML = "";
  for (const m of moduleCatalogue) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = m.id;
    cb.checked = checked ? checked.includes(m.id) : m.default;
    const desc = document.createElement("span");
    desc.className = "muted";
    desc.textContent = ` ${m.description}`;
    label.append(cb, ` ${m.name}`, desc);
    box.append(label);
  }
}

const areaMode = () =>
  (document.querySelector<HTMLInputElement>('input[name="import-area"]:checked')?.value ?? "all");
const setArea = (v: "all" | "rect") =>
  (document.querySelector<HTMLInputElement>(`input[name="import-area"][value="${v}"]`)!.checked = true);

function updateImportUi() {
  $("import-modules").hidden = importProfile() !== "custom";
  $("import-profile-desc").textContent = profiles.find((p) => p.id === importProfile())?.description ?? "";
  $("import-bbox").textContent = importRects.length
    ? `${importRects.length} prostokąt(y)` : "";
  $("import-clear").hidden = importRects.length === 0;
}

async function openImportDialog(m: MapInfo) {
  importTarget = m;
  importRects = [];
  if (!profiles.length) {
    try { profiles = await api.profiles(); } catch (e) { return toast(String(e)); }
  }
  if (!moduleCatalogue.length) moduleCatalogue = await api.modules().catch(() => []);
  const sel = $<HTMLSelectElement>("import-profile");
  sel.innerHTML = "";
  for (const p of profiles) sel.add(new Option(p.name, p.id));
  sel.value = profiles.some((p) => p.id === "classic") ? "classic" : profiles[0]?.id ?? "";
  renderModules(null);
  $<HTMLInputElement>("import-name").value = "";
  setArea("all");
  $("import-title").textContent = `Nowy eksport: ${m.name}`;
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
    const onMove = (e: maplibregl.MapMouseEvent) => {
      if (start) setDraft([...importRects, rectOf(start, e.lngLat)]);
    };
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
  if (b) importRects.push(b);
  setDraft(importRects);
  setArea(importRects.length ? "rect" : "all");
  updateImportUi();
  importDlg.showModal();
}

$("import-draw").addEventListener("click", () => void chooseRect());
$("import-clear").addEventListener("click", () => {
  importRects = [];
  setDraft([]);
  setArea("all");
  updateImportUi();
});
document.querySelectorAll<HTMLInputElement>('input[name="import-area"]').forEach((r) =>
  r.addEventListener("change", () => {
    if (areaMode() === "rect" && !importRects.length) void chooseRect();
    setDraft(areaMode() === "rect" ? importRects : []);
    updateImportUi();
  }));
$("import-profile").addEventListener("change", updateImportUi);
importDlg.addEventListener("close", () => { if (!sampleMode) setDraft([]); });

$("import-start").addEventListener("click", async () => {
  const m = importTarget;
  if (!m) return;
  const rect = areaMode() === "rect";
  const bboxes = rect ? importRects : null;
  if (rect && !importRects.length) return toast("Narysuj prostokąt albo wybierz „cały plik”");
  if (!rect && importProfile() !== "classic" && m.source_bytes > 300 * 1024 ** 2 &&
      !confirm(`Plik ma ${formatBytes(m.source_bytes)}. Ten profil dla całego pliku może trwać bardzo długo ` +
        `i zużyć dużo pamięci. Czy na pewno nie ograniczyć obszaru?\nOK = importuj cały plik mimo to.`)) return;
  try {
    const res = await api.createExport(m.id, {
      name: $<HTMLInputElement>("import-name").value.trim(),
      profile: importProfile(),
      modules: importModules(),
      bboxes,
      search: $<HTMLInputElement>("import-search").checked,
    });
    importDlg.close();
    toast("Eksport dodany do kolejki");
    await refreshMaps();
    await selectExport(res.export_id);  // opens by itself when the import finishes
    void refreshJobs();
  } catch (e) { toast(String(e)); }
});

// --- sample

function sampleStyleId(profile: string): string {
  const own = styles.find((s) => s.profiles.includes(profile));
  return (own ?? styles.find((s) => !s.profiles.length) ?? styles[0])?.id ?? "basic";
}

function sampleBBox(m: MapInfo): BBox {
  if (areaMode() === "rect" && importRects.length) return importRects[importRects.length - 1];
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
    const { job_id, bbox } = await api.createSample(m.id, profile, sampleBBox(m), importModules());
    void refreshJobs();
    const job = await waitForJob(job_id);
    if (job.status !== "done") throw new Error(job.message ?? "Nie udało się wygenerować próbki");
    sampleMode = { mapId: m.id, profile };
    importDlg.close();
    map.setStyle(`/api/maps/${m.id}/sample/style/${sampleStyleId(profile)}.json`);
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 30, duration: 0 });
    const name = profiles.find((p) => p.id === profile)?.name ?? profile;
    $("sample-text").textContent = `Próbka (ok. 10×10 km), profil „${name}”. Tak będzie wyglądał eksport.`;
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
  setDraft([]);
  if (sm) void api.deleteSample(sm.mapId).catch(() => undefined);
  await selectExport(current?.id ?? null);
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
    prog.textContent = "Wysłano. Ustaw parametry pierwszego eksportu.";
    await refreshMaps();
    const uploaded = maps.find((m) => m.id === res.map_id);
    if (uploaded) await openImportDialog(uploaded);
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
      export_id: current.id,
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
  lastJobs = jobs;
  const ul = $("jobs");
  ul.innerHTML = "";
  for (const j of jobs.slice(0, 15)) {
    const li = document.createElement("li");
    const label = { import: "Import", render: "Render", sample: "Próbka", geocode: "Indeks adresów" }[j.kind];
    const exp = exportsOf().find((e) => e.id === j.export_id);
    const mapName = exp ? exportLabel(exp) : maps.find((m) => m.id === j.map_id)?.name ?? "";
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
    else {
      const b = document.createElement("button");
      b.textContent = "✕";
      b.title = "Usuń z historii";
      b.onclick = () => api.deleteJob(j.id).then(refreshJobs);
      li.appendChild(b);
    }
    ul.appendChild(li);
  }
  $<HTMLButtonElement>("clear-jobs").hidden = !jobs.some(
    (j) => j.status !== "running" && j.status !== "queued");
  if (jobs.some((j) => j.kind === "import" || j.kind === "geocode")) refreshMaps();
}

$("clear-jobs").addEventListener("click", async () => {
  try { await api.clearJobs(); await refreshJobs(); } catch (e) { toast(String(e)); }
});

async function refreshOutputs() {
  const outs = await api.outputs();
  const ul = $("outputs");
  ul.innerHTML = "";
  for (const o of outs) {
    const li = document.createElement("li");
    const exp = exportsOf().find((e) => e.id === o.export_id);
    const mapName = exp ? exportLabel(exp) : maps.find((m) => m.id === o.map_id)?.name ?? o.map_id;
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
