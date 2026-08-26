// Fetch + decode of the server's little-endian binary bundles and JSON meta.
// Every value rendered by the dashboard arrives through here — nothing is
// synthesised client-side (rules 2 and 3).

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || payload.message || `${url} failed (${response.status})`);
  }
  return payload;
}

async function getBytes(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    let message = `${url} failed (${response.status})`;
    try {
      const payload = await response.json();
      if (payload && payload.error) message = payload.error;
    } catch {
      // binary or empty error body
    }
    throw new Error(message);
  }
  return response.arrayBuffer();
}

// Blob layout shared with the backend: u32 count, u32 offsets[count+1], f32 coords.
export function decodeLines(buffer) {
  const header = new DataView(buffer, 0, 4);
  const n = header.getUint32(0, true);
  const offsets = new Uint32Array(buffer, 4, n + 1);
  const coords = new Float32Array(buffer, 4 + (n + 1) * 4);
  return { n, offsets, coords };
}

// Product layout: i32 n, i32 flags(bit0 = dense ids), i32 lead,
// [u32 ids when not dense], u16 band_low[n], u16 band_high[n], u8 status[n].
const STATUS_BY_CODE = ["not_flooded", "flooded", "unknown"];

export function decodeProduct(buffer) {
  const view = new DataView(buffer, 0, 12);
  const n = view.getInt32(0, true);
  const dense = (view.getInt32(4, true) & 1) === 1;
  const lead = view.getInt32(8, true);
  let at = 12;
  let ids = null;
  if (!dense) {
    ids = new Uint32Array(buffer, at, n);
    at += n * 4;
  }
  const low = new Uint16Array(buffer, at, n);
  at += n * 2;
  const high = new Uint16Array(buffer, at, n);
  at += n * 2;
  const statusCodes = new Uint8Array(buffer, at, n);
  return {
    n,
    leadMinutes: lead,
    ids,
    low,
    high,
    codes: statusCodes,
    isFlooded: (i) => statusCodes[i] === 1,
    isModelled: (i) => statusCodes[i] !== 2,
    statusCodeName: (i) => STATUS_BY_CODE[statusCodes[i]] ?? "unknown",
  };
}

export async function loadState() {
  return getJson("/api/state");
}

export async function loadBasemapMeta() {
  return getJson("/api/basemap/meta");
}

export async function loadLines(url) {
  return decodeLines(await getBytes(url));
}

export async function loadProductFrame(lead) {
  return decodeProduct(await getBytes(`/api/product.bin?lead=${encodeURIComponent(lead)}`));
}

// Series frame layout: i32 index, i32 n, u16 band_low[n], u16 band_high[n],
// u8 status[n]. Segment ids are dense 1..n (checked server-side at parse).
export function decodeSeriesFrame(buffer) {
  const view = new DataView(buffer, 0, 8);
  const index = view.getInt32(0, true);
  const n = view.getInt32(4, true);
  let at = 8;
  const low = new Uint16Array(buffer, at, n);
  at += n * 2;
  const high = new Uint16Array(buffer, at, n);
  at += n * 2;
  const statusCodes = new Uint8Array(buffer, at, n);
  return {
    n,
    index,
    low,
    high,
    codes: statusCodes,
    isFlooded: (i) => statusCodes[i] === 1,
    isModelled: (i) => statusCodes[i] !== 2,
    statusCodeName: (i) => STATUS_BY_CODE[statusCodes[i]] ?? "unknown",
  };
}

export async function loadSeriesFrame(index) {
  return decodeSeriesFrame(
    await getBytes(`/api/product.bin?index=${encodeURIComponent(index)}`)
  );
}

export async function loadSeriesSegmentRow(index, segmentId) {
  const url =
    `/api/segments?index=${encodeURIComponent(index)}` +
    `&segment_id=${encodeURIComponent(segmentId)}`;
  const payload = await getJson(url);
  return payload.features?.[0] ?? null;
}

export async function loadSegmentRow(lead, segmentId) {
  const url = `/api/segments?lead=${encodeURIComponent(lead)}&segment_id=${encodeURIComponent(segmentId)}`;
  const payload = await getJson(url);
  return payload.features?.[0] ?? null;
}

// Road network client model: per-LOD geometry plus segment attributes.
// Segment ids are asserted dense (contract: dense keys 1..N); anything else
// falls back to an explicit id map rather than guessing.
export class RoadNetwork {
  constructor(meta) {
    const roads = meta.roads;
    this.meta = meta;
    this.nSegments = roads.n_segments;
    this.segmentIds = Int32Array.from(roads.segment_ids);
    this.classes = Uint8Array.from(roads.classes);
    this.nameIds = Int32Array.from(roads.name_ids);
    this.names = roads.distinct_names;
    this.partOwners = Int32Array.from(roads.part_owners);
    this.denseIds = true;
    for (let i = 0; i < this.segmentIds.length; i++) {
      if (this.segmentIds[i] !== i + 1) {
        this.denseIds = false;
        break;
      }
    }
    if (!this.denseIds) {
      this.indexById = new Map();
      for (let i = 0; i < this.segmentIds.length; i++) {
        this.indexById.set(this.segmentIds[i], i);
      }
    }
    this.lods = new Map(); // lod id -> decoded lines
    this.grids = new Map(); // lod id -> spatial grid over parts
    this.bbox = meta.bbox;
  }

  indexOfSegment(segmentId) {
    return this.denseIds ? segmentId - 1 : this.indexById.get(segmentId);
  }

  nameOfSegment(index) {
    const nameId = this.nameIds[index];
    return nameId >= 0 ? this.names[nameId] : null;
  }

  async ensureLod(id, urlFactory) {
    if (!this.lods.has(id)) {
      this.lods.set(id, await loadLines(urlFactory(id)));
    }
    return this.lods.get(id);
  }

  // Whole-city Path2D per road class in WORLD coordinates, built once per
  // LOD. At city zoom every part is visible anyway, so the renderer strokes
  // these through the view transform instead of rebuilding paths per frame.
  worldPaths(lodId, lines) {
    const key = lodId;
    let cached = this.worldPathCache?.get(key);
    if (cached) return cached;
    if (!this.worldPathCache) this.worldPathCache = new Map();
    const minor = new Path2D();
    const major = new Path2D();
    const owners = this.partOwners;
    const { offsets, coords } = lines;
    for (let part = 0; part < lines.n; part++) {
      const target = this.classes[owners[part]] === 1 ? major : minor;
      const start = offsets[part];
      const end = offsets[part + 1];
      target.moveTo(coords[start * 2], coords[start * 2 + 1]);
      for (let v = start + 1; v < end; v++) {
        target.lineTo(coords[v * 2], coords[v * 2 + 1]);
      }
    }
    cached = [minor, major];
    this.worldPathCache.set(key, cached);
    return cached;
  }

  grid(lodId, lines) {
    let grid = this.grids.get(lodId);
    if (!grid) {
      grid = buildPartGrid(lines, this.partOwners, this.bbox);
      this.grids.set(lodId, grid);
    }
    return grid;
  }

  // Parts grouped by owning segment (built once per LOD) so per-band world
  // paths can iterate segment-wise for the water layer.
  segPartRanges(lodId, lines) {
    let ranges = this.segPartCache?.get(lodId);
    if (ranges) return ranges;
    if (!this.segPartCache) this.segPartCache = new Map();
    const counts = new Uint32Array(this.nSegments);
    const owners = this.partOwners;
    for (let part = 0; part < lines.n; part++) counts[owners[part]]++;
    const segOffsets = new Uint32Array(this.nSegments + 1);
    for (let i = 0; i < this.nSegments; i++) segOffsets[i + 1] = segOffsets[i] + counts[i];
    const partIds = new Uint32Array(lines.n);
    const cursor = Uint32Array.from(segOffsets.subarray(0, this.nSegments));
    for (let part = 0; part < lines.n; part++) {
      partIds[cursor[owners[part]]++] = part;
    }
    ranges = { segOffsets, partIds };
    this.segPartCache.set(lodId, ranges);
    return ranges;
  }
}

// Uniform-grid index of line parts by their bounding boxes. Cell size in
// metres trades insert cost against per-query candidates.
function buildPartGrid(lines, partOwners, bbox, cellM = 512) {
  const [minx, miny, maxx, maxy] = bbox;
  const cols = Math.max(1, Math.ceil((maxx - minx) / cellM));
  const rows = Math.max(1, Math.ceil((maxy - miny) / cellM));
  const cells = new Map();
  const { offsets, coords, n } = lines;
  for (let part = 0; part < n; part++) {
    const start = offsets[part];
    const end = offsets[part + 1];
    if (end <= start) continue;
    let pxMin = Infinity, pyMin = Infinity, pxMax = -Infinity, pyMax = -Infinity;
    for (let v = start; v < end; v++) {
      const x = coords[v * 2];
      const y = coords[v * 2 + 1];
      if (x < pxMin) pxMin = x;
      if (x > pxMax) pxMax = x;
      if (y < pyMin) pyMin = y;
      if (y > pyMax) pyMax = y;
    }
    const c0 = Math.min(cols - 1, Math.max(0, ((pxMin - minx) / cellM) | 0));
    const c1 = Math.min(cols - 1, Math.max(0, ((pxMax - minx) / cellM) | 0));
    const r0 = Math.min(rows - 1, Math.max(0, ((pyMin - miny) / cellM) | 0));
    const r1 = Math.min(rows - 1, Math.max(0, ((pyMax - miny) / cellM) | 0));
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const key = r * cols + c;
        let bucket = cells.get(key);
        if (!bucket) cells.set(key, (bucket = []));
        bucket.push(part);
      }
    }
  }
  return { cellM, minx, miny, cols, rows, cells, maxCol: cols - 1, maxRow: rows - 1 };

}

export function queryGrid(grid, rect) {
  const c0 = Math.max(0, ((rect[0] - grid.minx) / grid.cellM) | 0);
  const c1 = Math.min(grid.maxCol, ((rect[2] - grid.minx) / grid.cellM) | 0);
  const r0 = Math.max(0, ((rect[1] - grid.miny) / grid.cellM) | 0);
  const r1 = Math.min(grid.maxRow, ((rect[3] - grid.miny) / grid.cellM) | 0);
  const seen = new Set();
  const out = [];
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const bucket = grid.cells.get(r * grid.cols + c);
      if (!bucket) continue;
      for (const part of bucket) {
        if (!seen.has(part)) {
          seen.add(part);
          out.push(part);
        }
      }
    }
  }
  return out;
}
