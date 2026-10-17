import { describe, expect, it } from "vitest";
import { bucketLabel, measureText, partialBucket, periodBars, seriesLine, ticks, trend } from "./groupview";
import { LabelSource, MeasureOp, type InstanceSummary } from "./om/model";

const chart = { width: 100, height: 10 };
const point = (start: string, count: number, sum?: number) => ({ start, count, sums: [{ value: sum }] });

describe("seriesLine", () => {
  it("最大の値が上端、0 が下端、点は横に等間隔", () => {
    const { segments, max } = seriesLine([point("2024-01-01", 0), point("2024-02-01", 5), point("2024-03-01", 10)],
                                         (p) => p.count, chart);
    expect(max).toBe(10);
    expect(segments).toEqual([[[0, 10], [50, 5], [100, 0]]]);
  });
  it("点が1つなら真ん中、すべて 0 なら下端", () => {
    expect(seriesLine([point("2024-01-01", 0)], (p) => p.count, chart).segments).toEqual([[[50, 10]]]);
  });
  it("値の無い区切りで線を切る（0 として描かない）", () => {
    const pts = [point("2024-01-01", 1, 4), point("2024-02-01", 0), point("2024-03-01", 1, 2)];
    expect(seriesLine(pts, (p) => p.sums[0]?.value, chart).segments).toEqual([[[0, 0]], [[100, 5]]]);
  });
});

const row = (key: string, from?: string, to?: string): InstanceSummary => ({
  table: "t", key: [key], label: key, label_source: LabelSource.own, cells: [],
  link_cells: [{ column: "startdate", value: from }, { column: "enddate", value: to }],
});

describe("periodBars", () => {
  const cells = (r: InstanceSummary) => r.link_cells ?? [];
  it("始まりと終わりを全体の幅に並べ、終わりの無い期間は右端まで（今も有効）", () => {
    const { bars, first, last } = periodBars([row("a", "2020-01-01", "2021-01-01"), row("b", "2021-01-01")],
                                             "startdate", "enddate", cells);
    expect(bars.map((b) => [b.from, b.to, b.open])).toEqual([[0, 1, false], [1, 1, true]]);
    expect([first, last]).toEqual(["2020-01-01", "2021-01-01"]);
  });
  it("終わりのセル自体が無い行は、今も有効とはみなさず読めない行にする", () => {
    const noEnd: InstanceSummary = { ...row("c", "2020-01-01"), link_cells: [{ column: "startdate", value: "2020-01-01" }] };
    expect(periodBars([noEnd], "startdate", "enddate", cells).unreadable.map((r) => r.key[0])).toEqual(["c"]);
  });
  it("日付として読めない行は帯にせず、別に返す", () => {
    const { bars, unreadable } = periodBars([row("a", "いつか", "2021-01-01"), row("b", "2020-01-01", "2021-01-01")],
                                            "startdate", "enddate", cells);
    expect(bars.map((b) => b.row.key[0])).toEqual(["b"]);
    expect(unreadable.map((r) => r.key[0])).toEqual(["a"]);
  });
});

describe("measureText", () => {
  it("合計・平均と単位", () => {
    expect(measureText({ column: "qty", op: MeasureOp.sum, value: "26", unit_text: "each" })).toBe("qty 合計 26 each");
    expect(measureText({ column: "price", op: MeasureOp.avg })).toBe("price 平均 —");
  });
});

describe("trend / ticks / bucketLabel / partialBucket", () => {
  it("最後と一つ前の区切りの増減。前が 0 なら率は無し", () => {
    expect(trend([point("2024-01-01", 10), point("2024-02-01", 15)])?.ratio).toBeCloseTo(0.5);
    expect(trend([point("2024-01-01", 0), point("2024-02-01", 5)])?.ratio).toBeUndefined();
    expect(trend([])).toBeNull();
  });
  it("目盛りは 0 から最大を覆う、切りのよい刻み", () => {
    expect(ticks(20)).toEqual([0, 5, 10, 15, 20]);
    expect(ticks(358)).toEqual([0, 100, 200, 300]);
    expect(ticks(0)).toEqual([0]);
  });
  it("区切りの見出し", () => {
    expect(bucketLabel("2024-05-01", "month")).toBe("2024-05");
    expect(bucketLabel("2024-01-01", "year")).toBe("2024");
  });
  it("最新の日が区切りの最後の日より前なら途中", () => {
    expect(partialBucket("2025-06-01", "month", "2025-06-29")).toBe(true);
    expect(partialBucket("2025-06-01", "month", "2025-06-30")).toBe(false);
    expect(partialBucket("2025-01-01", "year", "2025-12-31")).toBe(false);
    expect(partialBucket("2025-06-01", "day", "2025-06-01")).toBe(false);
  });
});
