// XYZ tile math, mirrors backend/app/tiles/xyz.py (keep both in sync).
export const MAX_LAT = 85.0511287798066;

export function lonLatToTile(lon: number, lat: number, z: number): [number, number] {
  const n = 2 ** z;
  lat = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
  lon = Math.max(-180, Math.min(180, lon));
  const x = Math.floor(((lon + 180) / 360) * n);
  const r = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n);
  return [Math.min(Math.max(x, 0), n - 1), Math.min(Math.max(y, 0), n - 1)];
}

export function tileToLonLat(x: number, y: number, z: number): [number, number] {
  const n = 2 ** z;
  const lon = (x / n) * 360 - 180;
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
  return [lon, lat];
}

/** Polygon ring of a tile, closed. */
export function tileRing(x: number, y: number, z: number): [number, number][] {
  const nw = tileToLonLat(x, y, z);
  const se = tileToLonLat(x + 1, y + 1, z);
  return [nw, [se[0], nw[1]], se, [nw[0], se[1]], nw];
}

export const key = (x: number, y: number) => `${x},${y}`;
export const unkey = (k: string): [number, number] => k.split(",").map(Number) as [number, number];

export function regrid(sel: Set<string>, fromZ: number, toZ: number): Set<string> {
  const out = new Set<string>();
  if (toZ >= fromZ) {
    const k = 2 ** (toZ - fromZ);
    for (const s of sel) {
      const [x, y] = unkey(s);
      for (let cx = x * k; cx < x * k + k; cx++)
        for (let cy = y * k; cy < y * k + k; cy++) out.add(key(cx, cy));
    }
  } else {
    const shift = fromZ - toZ;
    for (const s of sel) {
      const [x, y] = unkey(s);
      out.add(key(x >> shift, y >> shift));
    }
  }
  return out;
}

const AVG_BYTES_256: Record<string, number> = { png: 18000, png8: 8000, jpg: 14000 };

export function estimateBytes(nTiles: number, tilePx: number, fmt: string): number {
  return nTiles * (AVG_BYTES_256[fmt] ?? 18000) * (tilePx / 256) ** 2;
}

export function formatBytes(b: number): string {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
}
