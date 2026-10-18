import { describe, expect, it } from "vitest";
import { graticule, gridCells, type LonLat, viewStats } from "./geo";

// 決まった種の擬似乱数（テストを毎回同じにする）
function points(seed: number, n: number): LonLat[] {
  let s = seed;
  const next = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
  return Array.from({ length: n }, () => ({ lon: -180 + next() * 360, lat: -90 + next() * 180 }));
}

describe("gridCells", () => {
  it("どの点もちょうど1つの升に入る（件数の和 = 点の数、点は自分の升の中）", () => {
    for (const seed of [1, 2, 3, 42]) {
      const ps = points(seed, 500);
      const { size, cells } = gridCells(ps, 20);
      expect(cells.reduce((a, c) => a + c.count, 0)).toBe(ps.length);
      for (const p of ps) {
        const inside = cells.filter((c) => p.lon >= c.west - 1e-9 && p.lon <= c.west + size + 1e-9
                                           && p.lat >= c.south - 1e-9 && p.lat <= c.south + size + 1e-9);
        expect(inside.length).toBeGreaterThanOrEqual(1);
      }
      expect(cells.every((c) => c.count > 0)).toBe(true);
    }
  });
  it("点が無ければ升も無い。1点でも升が1つ", () => {
    expect(gridCells([]).cells).toEqual([]);
    expect(gridCells([{ lon: 10, lat: 20 }]).cells.map((c) => c.count)).toEqual([1]);
  });
});

describe("viewStats", () => {
  it("範囲の中の点だけを数え、上位の升の件数は範囲の中の点の数を超えない（多い順）", () => {
    for (const seed of [5, 6, 7]) {
      const ps = points(seed, 400);
      const box = { west: -60, south: -30, east: 90, north: 45 };
      const { inside, cells } = viewStats(ps, box, 10, 5);
      expect(inside).toBe(ps.filter((p) => p.lon >= -60 && p.lon <= 90 && p.lat >= -30 && p.lat <= 45).length);
      expect(cells.reduce((a, c) => a + c.count, 0)).toBeLessThanOrEqual(inside);
      expect(cells.map((c) => c.count)).toEqual([...cells.map((c) => c.count)].sort((a, b) => b - a));
    }
  });
  it("日付変更線をまたぐ範囲（東端 < 西端）", () => {
    const ps = [{ lon: 179, lat: 0 }, { lon: -179, lat: 0 }, { lon: 0, lat: 0 }];
    expect(viewStats(ps, { west: 170, south: -10, east: -170, north: 10 }).inside).toBe(2);
  });
});

describe("graticule", () => {
  it("赤道と本初子午線だけが major", () => {
    const major = graticule(10).features.filter((f) => f.properties.major);
    expect(major.length).toBe(2);
  });
});
