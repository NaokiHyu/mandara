// 近隣の集まり（1:n の子、relation を挟んだ n:m）。見せ方（GroupView）は API が OM と件数から決めたものに従う：
// 一覧 / 集計の頭出し / 時系列の線グラフ / 期間の帯。どの見せ方でも、件数・集計・最新の日・行への入り口を出す。
import { relatedTitle } from "./domain";
import { measureText, periodBars, seriesLine } from "./groupview";
import { GroupView, type DomainSummary, type InstanceSummary, type RelatedGroup } from "./om/model";
import { DomainIcon, Label } from "./parts";

const CHART = { width: 360, height: 72 };
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
      {g.view !== GroupView.list && <Totals group={g} />}
      {g.view === GroupView.time_series && <Series group={g} color={color} />}
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
      {g.latest !== undefined && (
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

/** 時系列の線グラフ（件数。合計できる尺度があれば、その1つ目も別の目盛りで重ねる）。 */
function Series({ group: g, color }: { group: RelatedGroup; color: string }) {
  const points = g.series ?? [];
  if (points.length === 0) return <p className="dim small">時系列の点が無い</p>;
  const counts = seriesLine(points, (p) => p.count, CHART);
  const measure = (g.series_measures ?? [])[0];
  const sums = measure === undefined ? null : seriesLine(points, (p) => p.sums[0]?.value, CHART); // 値の無い区切りで線を切る
  const d = (segments: [number, number][][]) => segments.map((path) => path.map(([x, y], i) =>
    `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ")).join(" ");
  const dots = counts.segments.flat();
  return (
    <figure className="series">
      <svg viewBox={`-4 -4 ${CHART.width + 8} ${CHART.height + 8}`} role="img"
           aria-label={`${g.time_column ?? ""} の${g.series_unit ?? ""}ごとの件数`}>
        <line x1={0} y1={CHART.height} x2={CHART.width} y2={CHART.height} className="axis" />
        {sums !== null && <path d={d(sums.segments)} className="line second" />}
        <path d={d(counts.segments)} className="line" style={{ stroke: `var(${color})` }} />
        {dots.length <= 40 && dots.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r={1.8} style={{ fill: `var(${color})` }}>
            <title>{`${points[i]?.start ?? ""}：${points[i]?.count ?? 0} 件`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="dim small">
        <span>{points[0]?.start}</span>
        <span style={{ color: `var(${color})` }}>━ 件数（最大 {count.format(counts.max)}）</span>
        {sums !== null && <span className="second-legend">━ {measure} 合計（最大 {count.format(sums.max)}）</span>}
        <span>{g.series_unit}ごと</span>
        <span>{points[points.length - 1]?.start}</span>
      </figcaption>
    </figure>
  );
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
                    style={{ left: `${b.from * 100}%`, width: `${Math.max(1, (b.to - b.from) * 100)}%`,
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
