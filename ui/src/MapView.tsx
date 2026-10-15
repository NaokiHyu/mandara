// 地図（MapLibre GL）。下地は同梱の Natural Earth（public/geo。外部のタイル配信を使わない）、点は API の PointSet。
// 色は theme.css のトークンから読む。文字のラベルは出さない（グリフの配信が要るため）。点を選ぶと行の主キーを出す。
import { AttributionControl, Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
// MapLibre は Worker の URL を実行時に組み立てるので、バンドラが Worker を見つけられない。
// Vite に Worker を（依存ごと）まとめさせ、その URL を明示的に渡す
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection, Point } from "geojson";
import { useEffect, useRef, useState } from "react";
import { getPoints } from "./api";
import type { GeoColumn } from "./geo";
import type { PointSet } from "./om/model";

setWorkerUrl(maplibreWorkerUrl);

type Load = { state: "loading" } | { state: "ok"; value: PointSet } | { state: "error"; error: unknown };

function token(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (value === "") throw new Error(`theme.css にトークン ${name} が無い`);
  return value;
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

export function MapView({ ontologyId, target }: { ontologyId: string; target: GeoColumn }) {
  const container = useRef<HTMLDivElement>(null);
  const [points, setPoints] = useState<Load>({ state: "loading" });
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    setPoints({ state: "loading" });
    setPicked(null);
    getPoints(ontologyId, target.tableId, target.column).then(
      (value) => setPoints({ state: "ok", value }),
      (error: unknown) => setPoints({ state: "error", error }),
    );
  }, [ontologyId, target]);

  useEffect(() => {
    if (container.current === null || points.state !== "ok") return;
    const data: FeatureCollection<Point, { key: string }> = {
      type: "FeatureCollection",
      features: points.value.points.map((p) => ({
        type: "Feature", properties: { key: p.key.join(", ") }, geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    };
    const map = new MapLibreMap({
      container: container.current,
      attributionControl: false,
      // 画面の画像化（スクリーンショット、印刷）で地図が空にならないように、描いた内容を保つ
      canvasContextAttributes: { preserveDrawingBuffer: true },
      style: {
        version: 8,
        sources: {
          // MapLibre は GeoJSON を Worker で取りに行くので、絶対 URL にする
          countries: { type: "geojson", data: new URL("/geo/countries.geojson", location.href).href },
          states: { type: "geojson", data: new URL("/geo/states.geojson", location.href).href },
          points: { type: "geojson", data },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": token("--bg") } },
          { id: "land", type: "fill", source: "countries", paint: { "fill-color": token("--panel") } },
          { id: "borders", type: "line", source: "countries", paint: { "line-color": token("--line-strong"), "line-width": 0.6 } },
          { id: "states", type: "line", source: "states", paint: { "line-color": token("--line"), "line-width": 0.4 } },
          {
            id: "points", type: "circle", source: "points",
            paint: {
              "circle-color": token("--accent"), "circle-opacity": 0.75,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 1.5, 8, 4],
            },
          },
        ],
      },
      center: [0, 20],
      zoom: 1,
    });
    map.addControl(new AttributionControl({ compact: true, customAttribution: "Natural Earth" }));
    const box = bounds(points.value.points);
    if (box !== null) map.fitBounds(box, { padding: 40, animate: false });
    map.on("click", "points", (event) => {
      const key = event.features?.[0]?.properties?.key;
      setPicked(typeof key === "string" ? key : null);
    });
    map.on("mouseenter", "points", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "points", () => { map.getCanvas().style.cursor = ""; });
    return () => map.remove();
  }, [points]);

  return (
    <div className="map-frame">
      <div ref={container} className="map" role="img" aria-label={`${target.tableName}.${target.column} の地図`} />
      <div className="map-status">
        {points.state === "loading" && <span className="dim">loading points…</span>}
        {points.state === "error" && (
          <span className="failure">
            {points.error instanceof Error ? `${points.error.name}: ${points.error.message}` : String(points.error)}
          </span>
        )}
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
            {picked !== null && <span className="dim"> · key {picked}</span>}
          </>
        )}
      </div>
    </div>
  );
}
