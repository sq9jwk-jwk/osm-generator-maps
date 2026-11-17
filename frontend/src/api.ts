export type BBox = [number, number, number, number];

export interface MapInfo {
  id: string;
  name: string;
  filename: string;
  size_bytes: number;
  status: "uploaded" | "importing" | "ready" | "failed";
  bbox: BBox | null;
  message: string | null;
}

export interface Job {
  id: string;
  kind: "import" | "render";
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

export interface Coverage { zoom: number; tiles: [number, number][]; }
export interface StyleInfo { id: string; name: string; }

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
  deleteMap: (id: string) => req("DELETE", `/api/maps/${id}`),
  coverage: (id: string) => req<Coverage>("GET", `/api/maps/${id}/coverage`),
  styles: () => req<StyleInfo[]>("GET", "/api/styles"),
  jobs: () => req<Job[]>("GET", "/api/jobs"),
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
