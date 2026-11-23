export type BBox = [number, number, number, number];

/** A folder of vector tiles made from a source file; this is what is browsed and rendered. */
export interface ExportInfo {
  id: string;
  map_id: string;
  name: string;
  status: "importing" | "ready" | "failed";
  message: string | null;
  profile: string;
  modules: string[] | null;
  region: BBox | null;
  regions: BBox[] | null;
  bbox: BBox | null;
  created_at: number;
  bytes: number;
  outputs: number;
}

/** A source: an uploaded .osm.pbf with the exports made from it. */
export interface MapInfo {
  id: string;
  name: string;
  filename: string;
  size_bytes: number;
  bbox: BBox | null;
  created_at: number;
  source_bytes: number;
  import_bytes: number;
  outputs: number;
  exports: ExportInfo[];
}

export interface DeleteResult { ok: boolean; freed_bytes: number; removed_outputs: number; }

export interface Job {
  id: string;
  kind: "import" | "render" | "sample";
  map_id: string | null;
  export_id: string | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  message: string | null;
}

export interface OutputInfo {
  job_id: string;
  map_id: string;
  export_id?: string | null;
  style: string;
  min_zoom: number;
  max_zoom: number;
  tile_size: number;
  format: string;
  scheme: string;
  tiles: number;
  size_bytes: number;
  created_at: string;
}

export interface MapContents {
  source_bytes: number;
  exports: ExportInfo[];
  outputs: OutputInfo[];
}

export interface NewExport {
  name: string;
  profile: string;
  modules: string[] | null;
  bboxes: BBox[] | null;
}

export interface Coverage { zoom: number; tiles: [number, number][]; }
export interface StyleInfo { id: string; name: string; profiles: string[]; }
export interface ProfileInfo { id: string; name: string; description: string; }
export interface ModuleInfo { id: string; name: string; description: string; default: boolean; }
export interface SampleInfo { exists: boolean; profile?: string; bbox?: BBox; }

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  maps: () => req<MapInfo[]>("GET", "/api/maps"),
  deleteMap: (id: string, opts: { outputs?: boolean } = {}) =>
    req<DeleteResult>("DELETE", `/api/maps/${id}?outputs=${!!opts.outputs}`),
  contents: (id: string) => req<MapContents>("GET", `/api/maps/${id}/contents`),
  deleteSource: (id: string) =>
    req<{ freed_bytes: number }>("DELETE", `/api/maps/${id}/parts/source`),
  profiles: () => req<ProfileInfo[]>("GET", "/api/profiles"),
  modules: () => req<ModuleInfo[]>("GET", "/api/modules"),
  createExport: (mapId: string, body: NewExport) =>
    req<{ export_id: string; job_id: string }>("POST", `/api/maps/${mapId}/exports`, body),
  deleteExport: (id: string, outputs = false) =>
    req<DeleteResult>("DELETE", `/api/exports/${id}?outputs=${outputs}`),
  exportLog: (id: string) => req<{ log: string }>("GET", `/api/exports/${id}/log`),
  coverage: (exportId: string) => req<Coverage>("GET", `/api/exports/${exportId}/coverage`),
  createSample: (id: string, profile: string, bbox: BBox, modules: string[] | null = null) =>
    req<{ job_id: string; bbox: BBox }>("POST", `/api/maps/${id}/sample`, { profile, bbox, modules }),
  sampleInfo: (id: string) => req<SampleInfo>("GET", `/api/maps/${id}/sample`),
  deleteSample: (id: string) => req("DELETE", `/api/maps/${id}/sample`),
  styles: () => req<StyleInfo[]>("GET", "/api/styles"),
  jobs: () => req<Job[]>("GET", "/api/jobs"),
  deleteJob: (id: string) => req("DELETE", `/api/jobs/${id}`),
  clearJobs: () => req<{ removed: number }>("DELETE", "/api/jobs"),
  cancelJob: (id: string) => req("POST", `/api/jobs/${id}/cancel`),
  outputs: () => req<OutputInfo[]>("GET", "/api/outputs"),
  deleteOutput: (id: string) => req("DELETE", `/api/outputs/${id}`),
  render: (body: unknown) => req<{ job_id: string; tiles: number }>("POST", "/api/jobs/render", body),
};

const CHUNK = 8 * 1024 * 1024;

/** Resumable chunked upload. Calls onProgress(0..1). */
export async function uploadPbf(file: File, onProgress: (p: number) => void): Promise<{ map_id: string }> {
  const init = await req<{ id: string; received: number }>("POST", "/api/uploads", {
    filename: file.name,
    size: file.size,
  });
  let offset = init.received;
  let retries = 0;
  while (offset < file.size) {
    const blob = file.slice(offset, offset + CHUNK);
    try {
      const res = await fetch(`/api/uploads/${init.id}?offset=${offset}`, { method: "PUT", body: blob });
      if (res.status === 409) {
        const j = await res.json();
        offset = j.detail.expected_offset;
        continue;
      }
      if (!res.ok) throw new Error(`${res.status}`);
      offset = (await res.json()).received;
      retries = 0;
      onProgress(offset / file.size);
    } catch (e) {
      if (++retries > 5) throw e;
      await new Promise((r) => setTimeout(r, 1000 * retries));
      const st = await req<{ received: number }>("GET", `/api/uploads/${init.id}`);
      offset = st.received;
    }
  }
  return req<{ map_id: string }>("POST", `/api/uploads/${init.id}/complete`);
}
