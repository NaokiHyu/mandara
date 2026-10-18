// 近隣の集まり（1:n の子、relation を挟んだ n:m）。見せ方（GroupView）は API が OM と件数から決めたものに従う：
// 一覧 / 集計の頭出し / 時系列の線グラフ / 期間の帯。どの見せ方でも、件数・集計・最新の日・行への入り口を出す。
import { groupKey, relatedTitle } from "./domain";
import { useEffect, useRef, useState } from "react";
import { amountText, bucketLabel, measureText, partialBucket, periodBars, seriesLine, trend } from "./groupview";
import { GroupView, MeasureOp, type DomainSummary, type GroupProfile, type InstanceSummary, type RelatedGroup } from "./om/model";
import { DomainIcon, Label } from "./parts";

const count = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 });

/** 集合の見せ方と集計（契約の GroupProfile。近隣の集まり RelatedGroup と、ドメインの集合 DomainRows が持つ）と、その行。 */
export type Profiled = GroupProfile & Pick<RelatedGroup, "rows" | "truncated">;

export function GroupPanel({ group: g, target, color, onOpen, onList }: {
  group: RelatedGroup;
  target: DomainSummary | undefined;
  color: string;
  onOpen: (r: InstanceSummary) => void;
  onList: () => void;
}) {
  const title = relatedTitle(g);
  return (
    <div className={`group view-${g.view}`} style={{ borderLeftColor: `var(${color})` }}>
      <h2>
        {target !== undefined && <DomainIcon kind={target.kind} color={color} />}
        <span className="mark" style={{ color: `var(${color})` }}>{title.mark}</span> {title.name}
        <span className="dim"> {title.detail}</span>
        <span className="count-badge">{count.format(g.count)}</span>
        {g.missing > 0 && <span className="failure small"> 先の行が無い {g.missing}</span>}
        {target !== undefined && (
          <button className="link small" onClick={onList}
                  title={g.kind === "children" ? "このインスタンスにつながる行だけの集合を開く" : "ドメイン全体の集合を開く（n:m の絞り込みは未対応）"}>
            {g.kind === "children" ? "この集合 →" : "一覧 →"}
          </button>
        )}
      </h2>
      {g.view === GroupView.time_series && <Series group={g} color={color} chartId={groupKey(g)} />}
      {g.view !== GroupView.list && <Totals group={g} />}
      {g.view === GroupView.timeline && <Timeline group={g} color={color} onOpen={onOpen} />}
      {g.view !== GroupView.timeline && <Rows group={g} onOpen={onOpen} />}
    </div>
  );
}

/** 集計の頭出し：尺度の集計、最新の日（今日でなければ基準日を明示）、enum の内訳、集計しなかった尺度。 */
export function Totals({ group: g }: { group: Profiled }) {
  // 時系列では合計は数字の帯に出るので、ここには合計以外（平均など）だけ
  const measures = (g.measures ?? []).filter((m) => g.view !== GroupView.time_series || m.op !== MeasureOp.sum);
  return (
    <div className="totals">
      {measures.length > 0 && (
        <p className="measures">{measures.map((m) => <span key={m.column}>{measureText(m)}</span>)}</p>
      )}
      {g.latest !== undefined && (g.view !== GroupView.time_series || (g.latest.measures ?? []).length > 0
                                   || (g.latest.snapshot ?? []).length > 0) && ( // 時系列では件数は数字の帯に出る
        <p className="latest">
          <span className={g.latest.is_today ? "today" : "dim"}>
            {g.latest.is_today ? "今日" : `最新日 ${g.latest.day}`}
          </span>
          <span> {count.format(g.latest.count)} 件</span>
          {(g.latest.measures ?? []).map((m) => <span key={m.column} className="dim"> · {measureText(m)}</span>)}
          {(g.latest.snapshot ?? []).length > 0 && (
            <span className="dim" title="半加法の尺度（残高・在庫など）は、最新の時点の行だけで合計する">
              {" "}· {g.latest.snapshot_at} 時点 {(g.latest.snapshot ?? []).map(measureText).join(" · ")}
            </span>
          )}
          {!g.latest.is_today && <span className="dim small" title="データが今日まで届いていないので、最新の日を基準にした"> （今日の行は無い）</span>}
        </p>
      )}
      {g.breakdown !== undefined && g.breakdown.length > 0 && (
        <p className="breakdown">
          <span className="dim">{g.breakdown_column}</span>
          {g.breakdown.map((b, i) => (
            <span key={`${b.value ?? ""}#${i}`} className="chip-static">{b.value ?? "—"} <b>{count.format(b.count)}</b></span>
          ))}
          {g.breakdown_truncated && <span className="dim">…</span>}
        </p>
      )}
      {(g.skipped_measures ?? []).length > 0 && (
        <p className="dim small">
          {(g.skipped_measures ?? []).map((s) => `${s.column}：${s.reason}`).join(" / ")}
        </p>
      )}
    </div>
  );
}

/** 時系列（詰めた形）：数字の帯1行と、高さ 40px の件数の面グラフ（カテゴリの色のグラデーション）。合計できる尺度の1つ目を点線で
 *  別の目盛りに重ねる。幅は枠に合わせて描き直す（伸び縮みで線が歪まない）。ホバーで十字線とその区切りの値。最新の区切りを強調し、
 *  前の区切りとの増減を出す。金額は短い表記（$16.1M）で、正確な値は title に出す。 */
export function Series({ group: g, color, chartId }: { group: Profiled; color: string; chartId: string }) {
  const points = g.series ?? [];
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (el === null) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  if (points.length === 0) return <p className="dim small">時系列の点が無い</p>;
  const pad = { left: 0, right: 30, top: 4, bottom: 2 };
  const chart = { width: Math.max(0, width - pad.left - pad.right), height: 38 };
  const counts = seriesLine(points, (p) => p.count, chart);
  const measure = (g.series_measures ?? [])[0];
  const sums = measure === undefined ? null : seriesLine(points, (p) => p.sums[0]?.value, chart);
  const line = (path: [number, number][]) => path.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const flat = counts.segments.flat();
  const area = counts.segments.map((path) => path.length === 0 ? "" :
    `${line(path)} L${path[path.length - 1]?.[0].toFixed(1)},${chart.height} L${path[0]?.[0].toFixed(1)},${chart.height} Z`).join(" ");
  const last = flat[flat.length - 1];
  const t = trend(points);
  const sumsShown = (g.measures ?? []).filter((m) => m.op === MeasureOp.sum);
  const measureUnit = (g.measures ?? []).find((m) => m.column === measure)?.unit_text;
  const partial = t !== null && partialBucket(t.last.start, g.series_unit, g.latest?.day);
  const gradient = `grad-${chartId}`.replace(/[^a-zA-Z0-9_-]/g, "_");
  const step = points.length > 1 ? chart.width / (points.length - 1) : chart.width;
  const at = (clientX: number, el: SVGSVGElement) => {
    const x = clientX - el.getBoundingClientRect().left - pad.left;
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(points.length > 1 ? x / step : 0))));
  };
  const shown = hover === null ? null : points[hover];
  const hx = hover === null ? 0 : flat[hover]?.[0] ?? 0;
  const per = unitName(g.series_unit);
  return (
    <figure className="series">
      <div className="kpis">
        <span className="kpi" title="件数の合計"><b>{count.format(g.count)}</b><i>件</i></span>
        {t !== null && (
          <span className="kpi" title={`最新の${per}（${g.time_column ?? ""}）`}>
            <i>{bucketLabel(t.last.start, g.series_unit)}</i>
            <b style={{ color: `var(${color})` }}>{count.format(t.last.count)}</b>
            {partial && <i title={`最新の日 ${g.latest?.day ?? ""} までの途中の値。前の区切りとは比べない`}>途中〜{g.latest?.day.slice(5)}</i>}
            {!partial && t.ratio !== undefined && (
              <span className={`kpi-delta ${t.ratio >= 0 ? "up" : "down"}`}>
                {t.ratio >= 0 ? "▲" : "▼"}{Math.abs(t.ratio * 100).toFixed(0)}%
              </span>
            )}
          </span>
        )}
        <span className="kpi" title={`${per}あたりの件数の最大`}><i>peak</i><b>{count.format(counts.max)}</b><i>/{per}</i></span>
        {sumsShown.map((m) => (
          <span key={m.column} className="kpi" title={measureText(m)}>
            <i>Σ{m.column}</i><b>{amountText(m.value, m.unit_text, true)}</b>
          </span>
        ))}
      </div>
      <div ref={box} className="chart-box">
        {width > 0 && (
          <svg width={width} height={chart.height + pad.top + pad.bottom} role="img"
               aria-label={`${g.time_column ?? ""} の${per}ごとの件数`}
               onMouseMove={(e) => at(e.clientX, e.currentTarget)} onMouseLeave={() => setHover(null)}>
            <defs>
              <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" style={{ stopColor: `var(${color})`, stopOpacity: 0.5 }} />
                <stop offset="100%" style={{ stopColor: `var(${color})`, stopOpacity: 0.02 }} />
              </linearGradient>
            </defs>
            <g transform={`translate(${pad.left},${pad.top})`}>
              <line x1={0} x2={chart.width} y1={0} y2={0} className="grid" />
              <line x1={0} x2={chart.width} y1={chart.height / 2} y2={chart.height / 2} className="grid" />
              <line x1={0} x2={chart.width} y1={chart.height} y2={chart.height} className="axis" />
              <text x={chart.width + 4} y={6} className="tick">{compact.format(counts.max)}</text>
              <text x={chart.width + 4} y={chart.height} className="tick">0</text>
              <path d={area} fill={`url(#${gradient})`} />
              {sums !== null && <path d={sums.segments.map(line).join(" ")} className="line second" />}
              <path d={counts.segments.map(line).join(" ")} className="line" style={{ stroke: `var(${color})` }} />
              {last !== undefined && (
                <>
                  <circle cx={last[0]} cy={last[1]} r={5} className="pulse" style={{ fill: `var(${color})` }} />
                  <circle cx={last[0]} cy={last[1]} r={2.2} style={{ fill: `var(${color})` }} />
                </>
              )}
              {shown !== null && shown !== undefined && (
                <g className="hover">
                  <line x1={hx} x2={hx} y1={0} y2={chart.height} />
                  <circle cx={hx} cy={flat[hover ?? 0]?.[1] ?? 0} r={2.5} style={{ stroke: `var(${color})` }} />
                </g>
              )}
            </g>
          </svg>
        )}
        {shown !== null && shown !== undefined && (
          <div className="tooltip" style={{ left: Math.min(Math.max(hx + pad.left, 60), Math.max(60, width - 60)) }}>
            <b>{bucketLabel(shown.start, g.series_unit)}</b>
            <span style={{ color: `var(${color})` }}>{count.format(shown.count)} 件</span>
            {measure !== undefined && <span className="dim">{measure} {shown.sums[0]?.value === undefined ? "—"
              : amountText(String(shown.sums[0].value), measureUnit)}</span>}
          </div>
        )}
      </div>
      <figcaption>
        <span>{bucketLabel(points[0]?.start ?? "", g.series_unit)}</span>
        <span className="legend-mini">
          <span><span className="swatch-line" style={{ background: `var(${color})` }} />件/{per} · {g.time_column}</span>
          {sums !== null && <span title={`別の目盛り。最大 ${amountText(String(sums.max), measureUnit)}`}>
            <span className="swatch-line dashed" />{measure}</span>}
        </span>
        <span>{bucketLabel(points[points.length - 1]?.start ?? "", g.series_unit)}</span>
      </figcaption>
    </figure>
  );
}

function unitName(unit: string | undefined): string {
  return unit === "day" ? "日" : unit === "month" ? "月" : unit === "year" ? "年" : "区切り";
}

/** 期間の帯。終わりが無い（NULL）期間は右端まで伸ばし、今も有効として印を付ける。 */
export function Timeline({ group: g, color, onOpen, note }: {
  group: Profiled; color: string; onOpen: (r: InstanceSummary) => void; note?: string;
}) {
  if (g.period_from === undefined || g.period_to === undefined) return null;
  const cells = (r: InstanceSummary) => [...(r.link_cells ?? []), ...r.cells];
  const { bars, unreadable, first, last } = periodBars(g.rows, g.period_from, g.period_to, cells);
  return (
    <div className="timeline">
      {note !== undefined && <p className="dim small">{note}</p>}
      <div className="timeline-scale dim small"><span>{first}</span><span>{last}</span></div>
      <ul>
        {bars.map((b, i) => (
          <li key={`${b.row.key.join("/")}#${i}`}>
            <button className="row-link" onClick={() => onOpen(b.row)}>
              <Label summary={b.row} />
            </button>
            <span className="track">
              <span className={`period-bar${b.open ? " open" : ""}`}
                    style={{ left: `${Math.min(b.from * 100, 99)}%`, // 短い期間も見えるように最低 1%。右端からはみ出さない
                             width: `${Math.max(1, (b.to - Math.min(b.from, 0.99)) * 100)}%`,
                             background: `var(${color})` }}
                    title={`${g.period_from} … ${b.open ? "（今も有効）" : g.period_to}`} />
            </span>
          </li>
        ))}
      </ul>
      {unreadable.length > 0 && <p className="failure small">期間を日付として読めない行 {unreadable.length}</p>}
      {g.truncated && <p className="dim small">先頭 {g.rows.length} 件（全 {count.format(g.count)} 件）</p>}
    </div>
  );
}

function Rows({ group: g, onOpen }: { group: RelatedGroup; onOpen: (r: InstanceSummary) => void }) {
  if (g.view === GroupView.time_series) { // 時系列ではグラフが主役。新しい行は見出しだけを1行に並べ、値は title に
    return (
      <p className="row-chips">
        <span className="dim">新しい {g.rows.length}</span>
        {g.rows.map((r, i) => (
          <button key={`${r.key.join("/")}#${i}`} className="row-chip" onClick={() => onOpen(r)}
                  title={r.cells.filter((c) => c.value !== undefined).map((c) => `${c.column} ${c.value ?? ""}`).join("\n")}>
            <Label summary={r} />
          </button>
        ))}
      </p>
    );
  }
  const caption = g.view === GroupView.list ? null
    : g.time_column !== undefined ? `新しい ${g.rows.length} 件` : `先頭 ${g.rows.length} 件`;
  return (
    <>
      {caption !== null && <p className="dim small rows-caption">{caption}</p>}
      <ul>
        {g.rows.map((r, i) => ( // via の集まりは relation の行ごと（同じ先が2回出ることがある）ので位置も鍵にする
          <li key={`${r.key.join("/")}#${i}`}>
            <button className="row-link" onClick={() => onOpen(r)}>
              <Label summary={r} />
              {r.link_cells !== undefined && r.link_cells.length > 0 && (
                <span className="link-cells" title="つながり（relation の行）の値。— は NULL">
                  {r.link_cells.map((c) => `${c.column} ${c.value ?? "—"}`).join(" · ")}
                </span>
              )}
              <span className="dim cells">
                {r.cells.filter((c) => c.value !== undefined).slice(0, 3)
                  .map((c) => `${c.column} ${c.value ?? ""}`).join(" · ")}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
