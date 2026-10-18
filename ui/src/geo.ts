// OM から、地図に出せる列（意味の型が geo.point の列）を見つける。
import { type OntologyDocument, ValueType } from "./om/model";

export interface GeoColumn {
  tableId: string;
  tableName: string;
  column: string;
}

export function geoColumns(doc: OntologyDocument): GeoColumn[] {
  const semanticTypes = doc.vocabulary.semantic_types ?? {};
  return doc.tables.flatMap((t) =>
    Object.values(t.columns ?? {})
      .filter((c) => c.semantic_type !== undefined && semanticTypes[c.semantic_type]?.type === ValueType.geoFULL_STOPpoint)
      .map((c) => ({ tableId: t.id, tableName: t.name, column: c.name })),
  );
}

export type LonLat = { lon: number; lat: number };

/** 柱（3D）のための格子：点の広がり（経度・緯度の幅の大きいほう）を about 個ほどに割った正方形の升に点を数える。
 *  点の無い升は作らない。どの点もちょうど1つの升に入る（升の西端・南端を含み、東端・北端を含まない。最後の升だけ東端・北端も含む）。 */
export function gridCells(points: readonly LonLat[], about = 48):
  { size: number; cells: { west: number; south: number; count: number }[] } {
  if (points.length === 0) return { size: 0, cells: [] };
  let [west, south, east, north] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of points) {
    west = Math.min(west, p.lon); east = Math.max(east, p.lon);
    south = Math.min(south, p.lat); north = Math.max(north, p.lat);
  }
  const size = Math.max(east - west, north - south, 1e-6) / about;
  const counts = new Map<string, { west: number; south: number; count: number }>();
  for (const p of points) {
    const i = Math.min(Math.floor((p.lon - west) / size), Math.floor((east - west) / size));
    const j = Math.min(Math.floor((p.lat - south) / size), Math.floor((north - south) / size));
    const k = `${i}/${j}`;
    const cell = counts.get(k) ?? { west: west + i * size, south: south + j * size, count: 0 };
    cell.count += 1;
    counts.set(k, cell);
  }
  return { size, cells: [...counts.values()] };
}

export type Box = { west: number; south: number; east: number; north: number };

/** 見えている範囲（box）の中の点の数と、その中で点の多い升の上位 top 個（升は gridCells の升。件数の多い順、同じなら西・南の順）。
 *  東端が西端より小さい box（日付変更線をまたぐ）は、経度を2つの区間として扱う。 */
export function viewStats(points: readonly LonLat[], box: Box, about = 48, top = 5):
  { inside: number; cells: { west: number; south: number; size: number; count: number }[] } {
  const lonIn = (lon: number) => box.west <= box.east ? lon >= box.west && lon <= box.east : lon >= box.west || lon <= box.east;
  const shown = points.filter((p) => lonIn(p.lon) && p.lat >= box.south && p.lat <= box.north);
  const { size, cells } = gridCells(shown, about);
  const ranked = [...cells].sort((a, b) => b.count - a.count || a.west - b.west || a.south - b.south).slice(0, top);
  return { inside: shown.length, cells: ranked.map((c) => ({ ...c, size })) };
}

/** 経緯線（step 度ごと）。赤道と本初子午線は major。 */
export function graticule(step = 10): { type: "FeatureCollection"; features: {
  type: "Feature"; properties: { major: boolean }; geometry: { type: "LineString"; coordinates: [number, number][] } }[] } {
  const features = [];
  for (let lon = -180; lon <= 180; lon += step) {
    const coordinates: [number, number][] = [];
    for (let lat = -85; lat <= 85; lat += 5) coordinates.push([lon, lat]);
    features.push({ type: "Feature" as const, properties: { major: lon === 0 }, geometry: { type: "LineString" as const, coordinates } });
  }
  for (let lat = -80; lat <= 80; lat += step) {
    const coordinates: [number, number][] = [];
    for (let lon = -180; lon <= 180; lon += 5) coordinates.push([lon, lat]);
    features.push({ type: "Feature" as const, properties: { major: lat === 0 }, geometry: { type: "LineString" as const, coordinates } });
  }
  return { type: "FeatureCollection", features };
}
