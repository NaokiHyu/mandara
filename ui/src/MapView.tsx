// 地図（MapLibre GL）。下地は同梱の Natural Earth（public/geo。外部のタイル配信を使わない）、点は API の PointSet。
// 色は theme.css のトークンから読む（点の色は、その表のドメインのカテゴリの色）。文字のラベルは出さない（グリフの配信が要るため）。
// 見え方：平面 / 鳥瞰（傾けて、ゆっくり回せる）/ 地球（globe）。レイヤ：光る点、ヒートマップ、クラスタ、柱（升ごとの件数の 3D）。
// 飾りではなく計器として：経緯線、線画の陸、テレメトリ（カーソル、中心、拡大率・傾き・向き、見えている点の数、密度の上位の升）。
// 点を選ぶと、右の Drawer にインスタンス（ドメインビューワと同じ部品）を出す。さらにたどるときはドメインの画面へ。
import { AttributionControl, type GeoJSONSource, Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
// MapLibre は Worker の URL を実行時に組み立てるので、バンドラが Worker を見つけられない。
// Vite に Worker を（依存ごと）まとめさせ、その URL を明示的に渡す
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection, Point, Polygon } from "geojson";
import { useEffect, useRef, useState } from "react";
import { getDomains, getPoints } from "./api";
import type { Place } from "./domain";
import { ErrorText, InstancePane, palette } from "./DomainView";
import { type Box, type GeoColumn, graticule, gridCells, viewStats } from "./geo";
import { shortName } from "./graph";
import type { DomainIndex, PointSet } from "./om/model";

setWorkerUrl(maplibreWorkerUrl);

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

const MODES = ["平面", "鳥瞰", "地球"] as const;
type Mode = (typeof MODES)[number];
const LAYERS = ["点", "ヒート", "クラスタ", "柱"] as const;
type Layer = (typeof LAYERS)[number];
/** レイヤの名前 → MapLibre のレイヤ ID。 */
const LAYER_IDS: Record<Layer, string[]> = {
  点: ["points-glow", "points"], ヒート: ["heat"], クラスタ: ["clusters", "clusters-halo"], 柱: ["columns"],
};
const ORBIT_DEG_PER_SEC = 4;

function token(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (value === "") throw new Error(`theme.css にトークン ${name} が無い`);
  return value;
}

/** #rrggbb を、不透明度つきの rgba にする（ヒートマップの色の傾斜のため）。 */
function rgba(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (m === null) throw new Error(`色を #rrggbb として読めない: ${hex}`);
  return `rgba(${parseInt(m[1] ?? "", 16)}, ${parseInt(m[2] ?? "", 16)}, ${parseInt(m[3] ?? "", 16)}, ${alpha})`;
}

function bounds(points: PointSet["points"]): [[number, number], [number, number]] | null {
  if (points.length === 0) return null;
  // 点が多いと Math.min(...xs) は引数の上限を超えうるので、1回なめて求める
  let [west, south, east, north] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of points) {
    west = Math.min(west, p.lon); east = Math.max(east, p.lon);
    south = Math.min(south, p.lat); north = Math.max(north, p.lat);
  }
  return [[west, south], [east, north]];
}

const count = new Intl.NumberFormat("en-US"); // 表示の書式をロケールに左右させない

export function MapView({ ontologyId, target, onOpenDomain }: {
  ontologyId: string;
  target: GeoColumn;
  onOpenDomain: (place: Place) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [points, setPoints] = useState<Load<PointSet>>({ state: "loading" });
  const [index, setIndex] = useState<Load<DomainIndex>>({ state: "loading" });
  const [picked, setPicked] = useState<string[] | null>(null);
  const [mode, setMode] = useState<Mode>("平面");
  const [orbit, setOrbit] = useState(false);
  const [layers, setLayers] = useState<ReadonlySet<Layer>>(new Set<Layer>(["点", "ヒート"]));
  const [columnMax, setColumnMax] = useState(0);
  const [live, setLive] = useState<MapLibreMap | null>(null); // 読み込みの終わった地図（計器と各設定が使う）。作り直すたびに変わる

  useEffect(() => {
    setPoints({ state: "loading" });
    setPicked(null);
    getPoints(ontologyId, target.tableId, target.column).then(
      (value) => setPoints({ state: "ok", value }),
      (error: unknown) => setPoints({ state: "error", error }),
    );
  }, [ontologyId, target]);

  useEffect(() => {
    getDomains(ontologyId).then((value) => setIndex({ state: "ok", value }),
                                (error: unknown) => setIndex({ state: "error", error }));
  }, [ontologyId]);

  // 点の色はドメインのカテゴリの色（ドメインの一覧が読めるまで地図を作らない）
  const colorVar = index.state === "ok" ? palette(index.value).color(target.tableId) : null;

  useEffect(() => {
    if (container.current === null || points.state !== "ok" || colorVar === null) return;
    const color = token(colorVar);
    const key = token("--text-key");
    const data: FeatureCollection<Point, { key: string }> = {
      type: "FeatureCollection",
      features: points.value.points.map((p) => ({
        type: "Feature", properties: { key: JSON.stringify(p.key) }, geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    };
    const grid = gridCells(points.value.points);
    const max = Math.max(1, ...grid.cells.map((c) => c.count));
    setColumnMax(max);
    const metersPerDegree = 111_000;
    const tallest = grid.size * metersPerDegree * 3; // 一番多い升の柱の高さ（升の幅の3倍）
    // 高さと色は件数の平方根で（件数が一つの升に偏ると、線形ではほかの柱がみな平らになる）
    const scale = (n: number) => Math.sqrt(n / max);
    const columns: FeatureCollection<Polygon, { count: number; height: number; t: number; cell: string }> = {
      type: "FeatureCollection",
      features: grid.cells.map((c) => {
        const inset = grid.size * 0.12; // 柱の間に隙間を空ける
        const [w, s, e, n] = [c.west + inset, c.south + inset, c.west + grid.size - inset, c.south + grid.size - inset];
        return {
          type: "Feature", properties: { count: c.count, height: scale(c.count) * tallest, t: scale(c.count),
                                         cell: JSON.stringify([c.west, c.south, c.west + grid.size, c.south + grid.size]) },
          geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
        };
      }),
    };
    const map = new MapLibreMap({
      container: container.current,
      attributionControl: false,
      // 画面の画像化（スクリーンショット、印刷）で地図が空にならないように、描いた内容を保つ
      canvasContextAttributes: { preserveDrawingBuffer: true },
      maxPitch: 75,
      style: {
        version: 8,
        sources: {
          // MapLibre は GeoJSON を Worker で取りに行くので、絶対 URL にする
          countries: { type: "geojson", data: new URL("/geo/countries.geojson", location.href).href },
          states: { type: "geojson", data: new URL("/geo/states.geojson", location.href).href },
          points: { type: "geojson", data },
          clustered: { type: "geojson", data, cluster: true, clusterRadius: 48, clusterMaxZoom: 12 },
          columns: { type: "geojson", data: columns },
          graticule: { type: "geojson", data: graticule(10) },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": token("--bg") } },
          // 陸は線画に寄せる（塗りは地よりわずかに明るいだけ。国境・州境を琥珀の細い線で）
          { id: "land", type: "fill", source: "countries", paint: { "fill-color": token("--panel"), "fill-opacity": 0.6 } },
          {
            id: "graticule", type: "line", source: "graticule",
            paint: {
              "line-color": ["case", ["get", "major"], rgba(token("--accent"), 0.45), rgba(token("--text-dim"), 0.18)],
              "line-width": ["case", ["get", "major"], 0.8, 0.5],
              "line-dasharray": [2, 3],
            },
          },
          { id: "borders", type: "line", source: "countries", paint: { "line-color": rgba(token("--accent"), 0.4), "line-width": 0.6 } },
          { id: "states", type: "line", source: "states", paint: { "line-color": rgba(token("--accent"), 0.14), "line-width": 0.4 } },
          {
            id: "heat", type: "heatmap", source: "points",
            paint: {
              "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 6, 6, 18, 12, 32],
              "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 0.15, 6, 0.4, 12, 1.2],
              "heatmap-opacity": 0.85,
              "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
                0, rgba(color, 0), 0.2, rgba(color, 0.25), 0.5, rgba(color, 0.6), 0.8, color, 1, key],
            },
          },
          {
            id: "columns", type: "fill-extrusion", source: "columns",
            paint: {
              "fill-extrusion-color": ["interpolate", ["linear"], ["get", "t"], 0, rgba(color, 0.55), 0.6, color, 1, key],
              "fill-extrusion-height": ["get", "height"],
              "fill-extrusion-opacity": 0.85,
            },
          },
          {
            id: "clusters-halo", type: "circle", source: "clustered", filter: ["has", "point_count"],
            paint: {
              "circle-color": color, "circle-opacity": 0.15, "circle-blur": 0.6,
              "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 16, 100, 30, 1000, 48],
            },
          },
          {
            id: "clusters", type: "circle", source: "clustered", filter: ["has", "point_count"],
            paint: {
              "circle-color": token("--panel-raised"), "circle-stroke-color": color, "circle-stroke-width": 1.5,
              "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 7, 100, 14, 1000, 22],
            },
          },
          {
            id: "points-glow", type: "circle", source: "points",
            paint: {
              "circle-color": color, "circle-opacity": 0.25, "circle-blur": 1,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 4, 8, 12],
            },
          },
          {
            id: "points", type: "circle", source: "points",
            paint: {
              "circle-color": color, "circle-opacity": 0.9,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 1.4, 8, 3.5, 14, 6],
            },
          },
          {
            id: "picked", type: "circle", source: "points", filter: ["==", ["get", "key"], ""],
            paint: {
              "circle-color": "rgba(0, 0, 0, 0)", "circle-stroke-color": key, "circle-stroke-width": 2,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 6, 8, 10],
            },
          },
        ],
      },
      center: [0, 20],
      zoom: 1,
    });
    map.addControl(new AttributionControl({ compact: true, customAttribution: "Natural Earth" }));
    const box = bounds(points.value.points);
    if (box !== null) map.fitBounds(box, { padding: 60, animate: false });
    map.on("load", () => setLive(map));
    map.on("click", (event) => {
      const shown = ["points", "clusters", "columns"].filter((id) => map.getLayoutProperty(id, "visibility") !== "none");
      const hit = map.queryRenderedFeatures(event.point, { layers: shown })[0];
      if (hit === undefined) return;
      if (hit.layer.id === "clusters") { // クラスタは、ほどける拡大率まで寄る
        const id = hit.properties.cluster_id as number;
        (map.getSource("clustered") as GeoJSONSource).getClusterExpansionZoom(id).then(
          (zoom) => map.easeTo({ center: (hit.geometry as Point).coordinates as [number, number], zoom, duration: 700 }),
          (error: unknown) => console.error("クラスタの拡大率を求められない", error),
        );
        return;
      }
      if (hit.layer.id === "columns") { // 柱は、その升まで寄る
        const [w, s, e, n] = JSON.parse(String(hit.properties.cell)) as [number, number, number, number];
        map.fitBounds([[w, s], [e, n]], { padding: 80, duration: 900, pitch: map.getPitch() });
        return;
      }
      const raw = hit.properties.key;
      if (typeof raw !== "string") return;
      setPicked(JSON.parse(raw) as string[]);
      map.easeTo({ center: (hit.geometry as Point).coordinates as [number, number], duration: 600,
                   padding: { right: Math.min(520, window.innerWidth * 0.45), left: 0, top: 0, bottom: 0 } });
    });
    for (const id of ["points", "clusters", "columns"]) {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    }
    return () => { setLive(null); map.remove(); };
  }, [points, colorVar]);

  // レイヤの表示
  useEffect(() => {
    const map = live; // 読み込みの終わった地図だけ（作り直した直後の、まだ読み込み中の地図には触らない）
    if (map === null) return;
    for (const layer of LAYERS) {
      for (const id of LAYER_IDS[layer]) map.setLayoutProperty(id, "visibility", layers.has(layer) ? "visible" : "none");
    }
  }, [layers, live]);

  // 選んだ点の輪
  useEffect(() => {
    const map = live; // 読み込みの終わった地図だけ（作り直した直後の、まだ読み込み中の地図には触らない）
    if (map === null) return;
    map.setFilter("picked", ["==", ["get", "key"], picked === null ? "" : JSON.stringify(picked)]);
  }, [picked, live]);

  // 見え方：平面 / 鳥瞰 / 地球
  useEffect(() => {
    const map = live; // 読み込みの終わった地図だけ（作り直した直後の、まだ読み込み中の地図には触らない）
    if (map === null) return;
    map.setProjection({ type: mode === "地球" ? "globe" : "mercator" });
    if (mode === "平面") map.easeTo({ pitch: 0, bearing: 0, duration: 900 });
    if (mode === "鳥瞰") map.easeTo({ pitch: 62, bearing: -24, duration: 1400 });
    if (mode === "地球") { // 点の広がりの中心を正面に、地球がほぼ画面いっぱいになるくらいに
      const box = points.state === "ok" ? bounds(points.value.points) : null;
      const center: [number, number] = box === null ? [0, 20] : [(box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2];
      map.easeTo({ center, pitch: 0, bearing: 0, zoom: 1.9, duration: 1400 });
    }
    if (mode === "平面") setOrbit(false);
  }, [mode, live, points]);

  // ゆっくり回す（触ったら止める）
  useEffect(() => {
    const map = live;
    if (map === null || !orbit) return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      map.setBearing(map.getBearing() + ((now - last) / 1000) * ORBIT_DEG_PER_SEC);
      last = now;
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    const stop = () => setOrbit(false);
    map.on("mousedown", stop);
    map.on("touchstart", stop);
    map.on("wheel", stop);
    return () => { cancelAnimationFrame(frame); map.off("mousedown", stop); map.off("touchstart", stop); map.off("wheel", stop); };
  }, [orbit, live]);

  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") setPicked(null); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  const toggle = (layer: Layer) => setLayers((now) => {
    const next = new Set(now);
    if (next.has(layer)) next.delete(layer); else next.add(layer);
    return next;
  });

  return (
    <div className="map-frame">
      <div ref={container} className="map" role="img" aria-label={`${target.tableName}.${target.column} の地図`} />
      <div className="hud-frame" aria-hidden="true">
        <span className="corner tl" /><span className="corner tr" /><span className="corner bl" /><span className="corner br" />
        <span className="crosshair" /><span className="scanlines" />
      </div>
      {live !== null && points.state === "ok" && colorVar !== null && (
        <Hud map={live} points={points.value.points} total={points.value.points.length}
             label={`${target.tableName}.${target.column}`} color={colorVar} hidden={picked !== null} />
      )}
      <div className="map-controls">
        <div className="segmented" role="group" aria-label="見え方">
          {MODES.map((m) => (
            <button key={m} className={mode === m ? "on" : ""} aria-pressed={mode === m} onClick={() => setMode(m)}>{m}</button>
          ))}
          <button className={orbit ? "on" : ""} aria-pressed={orbit} disabled={mode === "平面"}
                  title="ゆっくり回す（地図に触ると止まる）" onClick={() => setOrbit((o) => !o)}>⟳</button>
        </div>
        <div className="segmented" role="group" aria-label="レイヤ">
          {LAYERS.map((l) => (
            <button key={l} className={layers.has(l) ? "on" : ""} aria-pressed={layers.has(l)} onClick={() => toggle(l)}>{l}</button>
          ))}
        </div>
        {layers.has("柱") && mode === "平面" && <p className="dim small">柱は鳥瞰で立体に見える</p>}
      </div>
      {(layers.has("ヒート") || layers.has("柱")) && colorVar !== null && (
        <div className="map-legend">
          <span className="dim">少</span>
          <span className="ramp" style={{ background: `linear-gradient(90deg, transparent, var(${colorVar}), var(--text-key))` }} />
          <span className="dim">多</span>
          {layers.has("柱") && <span className="dim"> · 柱の高さは件数の平方根。最大 {count.format(columnMax)} 件 / 升</span>}
        </div>
      )}
      <div className="map-status">
        {(points.state === "loading" || index.state === "loading") && <span className="dim">loading points…</span>}
        {points.state === "error" && <ErrorText error={points.error} />}
        {index.state === "error" && <ErrorText error={index.error} />}
        {points.state === "ok" && (
          <>
            <span>{target.tableName}.{target.column} · {count.format(points.value.points.length)} points</span>
            {points.value.nulls > 0 && <span className="dim"> · {points.value.nulls} null</span>}
            {points.value.failed.length > 0 && (
              <span className="failure" title={points.value.failed.slice(0, 5).map((f) => `${f.key.join(",")}: ${f.reason}`).join("\n")}>
                {" "}· {points.value.failed.length} undecodable
              </span>
            )}
            {points.value.truncated && (
              <span className="warn"> · 上限 {count.format(points.value.limit)} 行で打ち切り（表にはまだ行がある）</span>
            )}
          </>
        )}
      </div>
      {picked !== null && index.state === "ok" && (
        <Drawer ontologyId={ontologyId} index={index.value} table={target.tableId} instanceKey={picked}
                onClose={() => setPicked(null)} onOpenDomain={onOpenDomain} />
      )}
    </div>
  );
}

function latText(lat: number): string { return `${lat >= 0 ? "N" : "S"} ${Math.abs(lat).toFixed(4).padStart(7, "0")}`; }
function lonText(lon: number): string { return `${lon >= 0 ? "E" : "W"} ${Math.abs(lon).toFixed(4).padStart(8, "0")}`; }

/** 計器（テレメトリ）：カーソルと中心の緯度・経度、拡大率・傾き・向き、見えている点の数、見えている範囲で点の多い升の上位。
 *  升を押すとそこへ寄る。地図の動きのたびに描き直すのはこの部品だけ（地図の画面全体は描き直さない）。 */
function Hud({ map, points, total, label, color, hidden }: {
  map: MapLibreMap; points: PointSet["points"]; total: number; label: string; color: string; hidden: boolean;
}) {
  const [cursor, setCursor] = useState<{ lon: number; lat: number } | null>(null);
  const [view, setView] = useState(() => ({ lon: map.getCenter().lng, lat: map.getCenter().lat, zoom: map.getZoom(),
                                             pitch: map.getPitch(), bearing: map.getBearing() }));
  const [stats, setStats] = useState<ReturnType<typeof viewStats> | null>(null);
  useEffect(() => {
    let frame = 0;
    const onMove = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setView({ lon: map.getCenter().lng, lat: map.getCenter().lat, zoom: map.getZoom(),
                                                    pitch: map.getPitch(), bearing: map.getBearing() }));
    };
    const onEnd = () => {
      const b = map.getBounds();
      const box: Box = { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() };
      const wraps = box.east - box.west >= 360;
      setStats(viewStats(points, wraps ? { ...box, west: -180, east: 180 } : {
        ...box, west: ((box.west + 540) % 360) - 180, east: ((box.east + 540) % 360) - 180 }));
    };
    const onCursor = (e: { lngLat: { lng: number; lat: number } }) => setCursor({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    const onLeave = () => setCursor(null);
    map.on("move", onMove);
    map.on("moveend", onEnd);
    map.on("mousemove", onCursor);
    map.on("mouseout", onLeave);
    onEnd();
    return () => {
      cancelAnimationFrame(frame);
      map.off("move", onMove); map.off("moveend", onEnd); map.off("mousemove", onCursor); map.off("mouseout", onLeave);
    };
  }, [map, points]);
  if (hidden) return null;
  const top = stats?.cells[0]?.count ?? 1;
  return (
    <div className="hud" style={{ ["--hud-color" as string]: `var(${color})` }}>
      <div className="hud-title"><span className="blink">●</span> TRACK <b>{label}</b></div>
      <dl>
        <dt>CURSOR</dt><dd>{cursor === null ? "—" : `${latText(cursor.lat)}  ${lonText(cursor.lon)}`}</dd>
        <dt>CENTER</dt><dd>{latText(view.lat)}  {lonText(view.lon)}</dd>
        <dt>ZOOM</dt><dd>{view.zoom.toFixed(2)} <i>PITCH</i> {view.pitch.toFixed(0)}° <i>BRG</i> {view.bearing.toFixed(0)}°</dd>
        <dt>IN VIEW</dt>
        <dd>{stats === null ? "—" : <>
          {count.format(stats.inside)} <i>/ {count.format(total)}</i>
          <span className="meter"><span style={{ width: `${total === 0 ? 0 : (stats.inside / total) * 100}%` }} /></span>
          <i>{total === 0 ? 0 : ((stats.inside / total) * 100).toFixed(1)}%</i>
        </>}</dd>
      </dl>
      {stats !== null && stats.cells.length > 0 && (
        <>
          <div className="hud-sub">DENSITY · TOP {stats.cells.length} <i>（升 {stats.cells[0]?.size.toFixed(2)}°）</i></div>
          <ol className="density">
            {stats.cells.map((c, i) => (
              <li key={`${c.west}/${c.south}`}>
                <button onClick={() => map.fitBounds([[c.west, c.south], [c.west + c.size, c.south + c.size]],
                                                     { padding: 80, duration: 900, pitch: map.getPitch() })}>
                  <i>{String(i + 1).padStart(2, "0")}</i>
                  <span>{latText(c.south + c.size / 2)} {lonText(c.west + c.size / 2)}</span>
                  <b>{count.format(c.count)}</b>
                  <span className="meter"><span style={{ width: `${(c.count / top) * 100}%` }} /></span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

/** 選んだ点のインスタンス（ドメインビューワと同じ部品）。中のリンクや「ドメインで開く」は、ドメインの画面へ移る。 */
function Drawer({ ontologyId, index, table, instanceKey, onClose, onOpenDomain }: {
  ontologyId: string;
  index: DomainIndex;
  table: string;
  instanceKey: readonly string[];
  onClose: () => void;
  onOpenDomain: (place: Place) => void;
}) {
  const { byTable, color } = palette(index);
  const domain = byTable.get(table);
  const hidden = index.hidden.find((h) => h.table === table);
  return (
    <aside className="drawer" aria-label="インスタンス">
      <header className="drawer-head">
        {domain === undefined && <span className="dim">{shortName(hidden?.name ?? table)} #{instanceKey.join("·")}</span>}
        {domain !== undefined && (
          <button className="link small" onClick={() => onOpenDomain({ table, key: [...instanceKey] })}>ドメインで開く →</button>
        )}
        <button className="close" aria-label="閉じる" title="閉じる（Esc）" onClick={onClose}>✕</button>
      </header>
      <div className="drawer-body">
        {domain === undefined ? (
          <p className="failure">{hidden === undefined ? `${table} はドメインの一覧に無い`
            : `${shortName(hidden.name)} はドメインではないので開けない：${hidden.reason}`}</p>
        ) : (
          <InstancePane key={`${table}/${instanceKey.join("/")}`} ontologyId={ontologyId} domain={domain}
                        instanceKey={instanceKey} known={byTable} color={color} onOpen={onOpenDomain} />
        )}
      </div>
    </aside>
  );
}
