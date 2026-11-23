export type BBox = [number, number, number, number];

export interface MapInfo {
  id: string;
  name: string;
  filename: string;
  size_bytes: number;
  status: "uploaded" | "importing" | "ready" | "failed";
  bbox: BBox | null;
  message: string | null;
  profile: string;
  modules: string[] | null;
  region: BBox | null;
  regions: BBox[] | null;
  created_at: number;
  source_bytes: number;
  import_bytes: number;
  outputs: number;
}

export interface DeleteResult { ok: boolean; freed_bytes: number; removed_outputs: number; }

export interface Job {
  id: string;
  kind: "import" | "render" | "sample";
  map_id: string | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  message: string | null;
}

export interface OutputInfo {
  job_id: string;
  map_id: string;
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

export type MapPart = "source" | "import" | "log";
export interface ArchivedImport {
  id: string;
  profile: string | null;
  modules?: string[] | null;
  region: BBox | null;
  bbox: BBox | null;
  created_at: number;
  size_bytes: number;
}
export interface MapContents {
  parts: { key: MapPart; label: string; bytes: number }[];
  outputs: OutputInfo[];
  imports: ArchivedImport[];
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
  deleteMap: (id: string, opts: { importOnly?: boolean; outputs?: boolean } = {}) =>
    req<DeleteResult>(
      "DELETE",
      `/api/maps/${id}?import_only=${!!opts.importOnly}&outputs=${!!opts.outputs}`,
    ),
  contents: (id: string) => req<MapContents>("GET", `/api/maps/${id}/contents`),
  deletePart: (id: string, part: MapPart) =>
    req<{ freed_bytes: number }>("DELETE", `/api/maps/${id}/parts/${part}`),
  profiles: () => req<ProfileInfo[]>("GET", "/api/profiles"),
  modules: () => req<ModuleInfo[]>("GET", "/api/modules"),
  importMap: (id: string, profile: string, bbox: BBox | null, merge = false, modules: string[] | null = null) =>
    req<{ job_id: string }>("POST", `/api/maps/${id}/import`, { profile, bbox, merge, modules }),
  createSample: (id: string, profile: string, bbox: BBox, modules: string[] | null = null) =>
    req<{ job_id: string; bbox: BBox }>("POST", `/api/maps/${id}/sample`, { profile, bbox, modules }),
  sampleInfo: (id: string) => req<SampleInfo>("GET", `/api/maps/${id}/sample`),
  deleteSample: (id: string) => req("DELETE", `/api/maps/${id}/sample`),
  activateImport: (id: string, imp: string) =>
    req("POST", `/api/maps/${id}/imports/${imp}/activate`),
  deleteImport: (id: string, imp: string) =>
    req<{ freed_bytes: number }>("DELETE", `/api/maps/${id}/imports/${imp}`),
  reimport: (id: string) => req<{ job_id: string }>("POST", `/api/maps/${id}/reimport`),
  importLog: (id: string) => req<{ log: string }>("GET", `/api/maps/${id}/log`),
  coverage: (id: string) => req<Coverage>("GET", `/api/maps/${id}/coverage`),
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
