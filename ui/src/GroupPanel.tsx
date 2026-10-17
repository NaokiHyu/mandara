// 近隣の集まり（1:n の子、relation を挟んだ n:m）。見せ方（GroupView）は API が OM と件数から決めたものに従う：
// 一覧 / 集計の頭出し / 時系列の線グラフ / 期間の帯。どの見せ方でも、件数・集計・最新の日・行への入り口を出す。
import { relatedTitle } from "./domain";
import { useEffect, useRef, useState } from "react";
import { amountText, bucketLabel, measureText, partialBucket, periodBars, seriesLine, ticks, trend } from "./groupview";
import { GroupView, type DomainSummary, type InstanceSummary, type RelatedGroup } from "./om/model";
import { DomainIcon, Label } from "./parts";

const count = new Intl.NumberFormat("en-US");

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
        {target !== undefined && <button className="link small" onClick={onList}>一覧 →</button>}
      </h2>
      {g.view === GroupView.time_series && <Series group={g} color={color} />}
      {g.view !== GroupView.list && <Totals group={g} />}
      {g.view === GroupView.timeline && <Timeline group={g} color={color} onOpen={onOpen} />}
      {g.view !== GroupView.timeline && <Rows group={g} onOpen={onOpen} />}
    </div>
  );
}

/** 集計の頭出し：尺度の集計、最新の日（今日でなければ基準日を明示）、enum の内訳、集計しなかった尺度。 */
function Totals({ group: g }: { group: RelatedGroup }) {
  return (
    <div className="totals">
      {(g.measures ?? []).length > 0 && (
        <p className="measures">{(g.measures ?? []).map((m) => <span key={m.column}>{measureText(m)}</span>)}</p>
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

/** 時系列：件数の面グラフ（カテゴリの色のグラデーション）、合計できる尺度の1つ目を点線で別の目盛りに重ねる。
 *  幅は枠に合わせて描き直す（伸び縮みで線が歪まない）。ホバーで十字線とその区切りの値。最新の区切りを強調し、前の区切りとの増減を出す。 */
function Series({ group: g, color }: { group: RelatedGroup; color: string }) {
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
  const pad = { left: 4, right: 44, top: 14, bottom: 22 };
  const chart = { width: Math.max(0, width - pad.left - pad.right), height: 132 };
  const counts = seriesLine(points, (p) => p.count, chart);
  const measure = (g.series_measures ?? [])[0];
  const sums = measure === undefined ? null : seriesLine(points, (p) => p.sums[0]?.value, chart);
  const line = (path: [number, number][]) => path.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const flat = counts.segments.flat();
  const area = counts.segments.map((path) => path.length === 0 ? "" :
    `${line(path)} L${path[path.length - 1]?.[0].toFixed(1)},${chart.height} L${path[0]?.[0].toFixed(1)},${chart.height} Z`).join(" ");
  const last = flat[flat.length - 1];
  const t = trend(points);
  const headline = (g.measures ?? []).find((m) => m.op === "sum" && m.column === measure);
  const measureUnit = (g.measures ?? []).find((m) => m.column === measure)?.unit_text;
  const partial = t !== null && partialBucket(t.last.start, g.series_unit, g.latest?.day);
  const gradient = `grad-${g.relation}-${g.via_table ?? ""}`.replace(/[^a-zA-Z0-9_-]/g, "_");
  const step = points.length > 1 ? chart.width / (points.length - 1) : chart.width;
  const at = (clientX: number, el: SVGSVGElement) => {
    const x = clientX - el.getBoundingClientRect().left - pad.left;
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(points.length > 1 ? x / step : 0))));
  };
  const shown = hover === null ? null : points[hover];
  const hx = hover === null ? 0 : flat[hover]?.[0] ?? 0;
  return (
    <figure className="series">
      <div className="kpis">
        <div className="kpi"><span className="kpi-label">合計</span><span className="kpi-value">{count.format(g.count)}</span>
          <span className="kpi-unit">件</span></div>
        {t !== null && (
          <div className="kpi">
            <span className="kpi-label">最新の{unitName(g.series_unit)} {bucketLabel(t.last.start, g.series_unit)}</span>
            <span className="kpi-value" style={{ color: `var(${color})` }}>{count.format(t.last.count)}</span>
            {partial && <span className="kpi-unit" title={`最新の日 ${g.latest?.day ?? ""} までの途中の値。前の区切りとは比べない`}>
              途中（〜{g.latest?.day.slice(5)}）</span>}
            {!partial && t.ratio !== undefined && (
              <span className={`kpi-delta ${t.ratio >= 0 ? "up" : "down"}`}>
                {t.ratio >= 0 ? "▲" : "▼"} {Math.abs(t.ratio * 100).toFixed(0)}%
              </span>
            )}
          </div>
        )}
        <div className="kpi"><span className="kpi-label">ピーク</span><span className="kpi-value">{count.format(counts.max)}</span>
          <span className="kpi-unit">件 / {unitName(g.series_unit)}</span></div>
        {headline !== undefined && (
          <div className="kpi"><span className="kpi-label">{headline.column} 合計</span>
            <span className="kpi-value">{amountText(headline.value, headline.unit_text)}</span></div>
        )}
      </div>
      <div ref={box} className="chart-box">
        {width > 0 && (
          <svg width={width} height={chart.height + pad.top + pad.bottom} role="img"
               aria-label={`${g.time_column ?? ""} の${unitName(g.series_unit)}ごとの件数`}
               onMouseMove={(e) => at(e.clientX, e.currentTarget)} onMouseLeave={() => setHover(null)}>
            <defs>
              <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" style={{ stopColor: `var(${color})`, stopOpacity: 0.45 }} />
                <stop offset="100%" style={{ stopColor: `var(${color})`, stopOpacity: 0 }} />
              </linearGradient>
            </defs>
            <g transform={`translate(${pad.left},${pad.top})`}>
              {ticks(counts.max).map((v) => {
                const y = counts.max === 0 ? chart.height : chart.height - (v / counts.max) * chart.height;
                return (
                  <g key={v}>
                    <line x1={0} x2={chart.width} y1={y} y2={y} className={v === 0 ? "axis" : "grid"} />
                    <text x={chart.width + 6} y={y + 3} className="tick">{count.format(v)}</text>
                  </g>
                );
              })}
              <path d={area} fill={`url(#${gradient})`} />
              {sums !== null && <path d={sums.segments.map(line).join(" ")} className="line second" />}
              <path d={counts.segments.map(line).join(" ")} className="line" style={{ stroke: `var(${color})` }} />
              {last !== undefined && (
                <>
                  <circle cx={last[0]} cy={last[1]} r={7} className="pulse" style={{ fill: `var(${color})` }} />
                  <circle cx={last[0]} cy={last[1]} r={3.2} style={{ fill: `var(${color})` }} />
                </>
              )}
              {shown !== null && shown !== undefined && (
                <g className="hover">
                  <line x1={hx} x2={hx} y1={0} y2={chart.height} />
                  <circle cx={hx} cy={flat[hover ?? 0]?.[1] ?? 0} r={3.5} style={{ stroke: `var(${color})` }} />
                </g>
              )}
              {[0, Math.floor((points.length - 1) / 2), points.length - 1].filter((i, k, a) => a.indexOf(i) === k).map((i) => (
                <text key={i} x={points.length > 1 ? i * step : chart.width / 2} y={chart.height + 15}
                      className="tick" textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}>
                  {bucketLabel(points[i]?.start ?? "", g.series_unit)}
                </text>
              ))}
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
      <figcaption className="dim small">
        <span><span className="swatch-line" style={{ background: `var(${color})` }} /> 件数（{unitName(g.series_unit)}ごと、{g.time_column}）</span>
        {sums !== null && <span><span className="swatch-line dashed" /> {measure} の合計（別の目盛り。最大 {amountText(String(sums.max), measureUnit)}）</span>}
      </figcaption>
    </figure>
  );
}

function unitName(unit: string | undefined): string {
  return unit === "day" ? "日" : unit === "month" ? "月" : unit === "year" ? "年" : "区切り";
}

/** 期間の帯。終わりが無い（NULL）期間は右端まで伸ばし、今も有効として印を付ける。 */
function Timeline({ group: g, color, onOpen }: { group: RelatedGroup; color: string; onOpen: (r: InstanceSummary) => void }) {
  if (g.period_from === undefined || g.period_to === undefined) return null;
  const cells = (r: InstanceSummary) => [...(r.link_cells ?? []), ...r.cells];
  const { bars, unreadable, first, last } = periodBars(g.rows, g.period_from, g.period_to, cells);
  return (
    <div className="timeline">
      <div className="timeline-scale dim small"><span>{first}</span><span>{last}</span></div>
      <ul>
        {bars.map((b, i) => (
          <li key={`${b.row.key.join("/")}#${i}`}>
            <button className="row-link" onClick={() => onOpen(b.row)}>
              <Label summary={b.row} />
            </button>
            <span className="track">
              <span className={`bar${b.open ? " open" : ""}`}
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
