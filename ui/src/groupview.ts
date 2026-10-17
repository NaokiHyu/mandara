// 近隣の集まりの見せ方の純粋な部分：線グラフの座標と、期間の帯の配置。描くのは GroupPanel.tsx。
import type { InstanceSummary, MeasureValue, SeriesPoint } from "./om/model";

export type Chart = { width: number; height: number };

/** 時系列の線：値のある点の [x, y] を、値の無い点で切った区間（segments）に分ける（SVG の座標。上が 0）。
 *  横は区切りの位置（API が空の区切りを埋めるので等間隔が時間に比例する）、縦は値の最大で合わせる。点が1つなら真ん中。 */
export function seriesLine(points: readonly SeriesPoint[], pick: (p: SeriesPoint) => number | undefined, chart: Chart):
  { segments: [number, number][][]; max: number } {
  const values = points.map(pick);
  const max = Math.max(0, ...values.filter((v): v is number => v !== undefined));
  const n = points.length;
  const segments: [number, number][][] = [];
  let current: [number, number][] = [];
  values.forEach((v, i) => {
    if (v === undefined) {
      if (current.length > 0) segments.push(current);
      current = [];
      return;
    }
    current.push([n === 1 ? chart.width / 2 : (i / (n - 1)) * chart.width,
                  max === 0 ? chart.height : chart.height - (v / max) * chart.height]);
  });
  if (current.length > 0) segments.push(current);
  return { segments, max };
}

export type Bar = { row: InstanceSummary; from: number; to: number; open: boolean; label: string };

/** 期間の帯：始まりと終わりの日付（表示の文字列 YYYY-MM-DD…）から、0..1 の位置。終わりが NULL なら右端まで（今も有効）。
 *  日付として読めない値の行は帯にしない（理由は呼ぶ側が出す）。 */
export function periodBars(rows: readonly InstanceSummary[], from: string, to: string,
                           cells: (r: InstanceSummary) => readonly { column: string; value?: string }[]):
  { bars: Bar[]; unreadable: InstanceSummary[]; first?: string; last?: string } {
  const parsed = rows.map((row) => {
    const cell = (c: string) => cells(row).find((x) => x.column === c);
    const start = day(cell(from)?.value);
    // 終わりのセルがあって値が無い（NULL）なら今も有効。セル自体が無ければ読めない（今も有効とはみなさない）
    const endCell = cell(to);
    const end = endCell === undefined ? undefined : endCell.value === undefined ? null : day(endCell.value);
    return { row, start, end };
  });
  const ok = parsed.filter((p) => p.start !== undefined && p.end !== undefined);
  const unreadable = parsed.filter((p) => p.start === undefined || p.end === undefined).map((p) => p.row);
  if (ok.length === 0) return { bars: [], unreadable };
  const times = ok.flatMap((p) => [p.start as number, ...(p.end === null ? [] : [p.end as number])]);
  const lo = Math.min(...times);
  const hi = Math.max(...times);
  const span = hi - lo || 1;
  const bars = ok.map((p) => ({
    row: p.row, from: ((p.start as number) - lo) / span, to: p.end === null ? 1 : ((p.end as number) - lo) / span,
    open: p.end === null, label: p.row.label,
  }));
  return { bars, unreadable, first: iso(lo), last: iso(hi) };
}

const CURRENCIES = new Set(["USD", "JPY"]);

/** 尺度の値の表示：通貨は通貨の書式（$16,084,942.55）、ほかは桁区切り（小数は 4 桁まで）。数として読めなければそのまま。 */
export function amountText(value: string | undefined, unit: string | undefined): string {
  if (value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (unit !== undefined && CURRENCIES.has(unit)) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: unit }).format(n);
  }
  const text = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(n);
  return unit === undefined ? text : `${text} ${unit}`;
}

/** 尺度の1行の表示：「subtotal 合計 $16,084,942.55」「orderqty 合計 26 each」。 */
export function measureText(m: MeasureValue): string {
  const op = m.op === "sum" ? "合計" : "平均";
  return `${m.column} ${op} ${amountText(m.value, m.unit_text)}`;
}

function day(text: string | undefined): number | undefined | null {
  if (text === undefined) return undefined;
  const t = Date.parse(text.slice(0, 10));
  return Number.isNaN(t) ? undefined : t;
}

function iso(t: number): string {
  return new Date(t).toISOString().slice(0, 10);
}

/** 最後の区切りと、その一つ前の区切りの件数の増減（前が 0 なら率は無し）。 */
export function trend(points: readonly SeriesPoint[]): { last: SeriesPoint; previous?: SeriesPoint; ratio?: number } | null {
  const last = points[points.length - 1];
  if (last === undefined) return null;
  const previous = points[points.length - 2];
  if (previous === undefined || previous.count === 0) return { last, previous };
  return { last, previous, ratio: (last.count - previous.count) / previous.count };
}

/** 目盛り：0 から最大までを、切りのよい刻み（1・2・5 × 10^n）で 3〜5 本に分ける。 */
export function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

/** 区切りの見出し（月なら 2024-05、年なら 2024、日ならそのまま）。 */
export function bucketLabel(start: string, unit: string | undefined): string {
  return unit === "year" ? start.slice(0, 4) : unit === "month" ? start.slice(0, 7) : start;
}

/** 最後の区切りが途中までか：最新の日（latest.day）が、その区切りの最後の日より前。途中の区切りとの増減は比べられない。 */
export function partialBucket(start: string, unit: string | undefined, latestDay: string | undefined): boolean {
  if (latestDay === undefined || unit === undefined || unit === "day") return false;
  const s = new Date(`${start}T00:00:00Z`);
  const end = unit === "month" ? new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth() + 1, 0))
    : new Date(Date.UTC(s.getUTCFullYear(), 11, 31));
  return latestDay < end.toISOString().slice(0, 10);
}
