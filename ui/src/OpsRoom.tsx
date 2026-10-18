// 運用室（壁面。モック 1a）：OM とソースの今を1枚で。数字はすべて API の実データ（作り物の数字は出さない）。
// - 左：出来事（event）のドメインの件数のカード（時系列のあるものを先に、多い順に4つ）。最新の区切りと前との増減、時系列の小さな棒
// - 中央：地図（地図の画面と同じ部品）。下に、一番大きい出来事の時系列と、enum の内訳
// - 右：レビュー待ち（未決の数、対象の内訳、影響の大きい問い）と OM の要約
// モックのウォッチと承認カードは、裏のデータ（仕様 8 章）がまだ無いので出さない。
import { lazy, Suspense, useEffect, useState } from "react";
import { getDomainRows, getDomains, getReview } from "./api";
import { orderEvents, type Place } from "./domain";
import { ErrorText, palette } from "./DomainView";
import { type GeoColumn } from "./geo";
import { shortName } from "./graph";
import { Series, Totals } from "./GroupPanel";
import { bucketLabel, partialBucket, sparkBars, trend } from "./groupview";
import type { DomainIndex, DomainRows, DomainSummary, OntologyDocument, ReviewQueue } from "./om/model";
import { Corners, DomainIcon } from "./parts";
import { asSubject, countBySubject, SUBJECT_LABEL, SUBJECTS } from "./review";

const MapView = lazy(() => import("./MapView").then((m) => ({ default: m.MapView })));

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

const count = new Intl.NumberFormat("en-US");
const EVENT_CARDS = 4;

export function OpsRoom({ ontologyId, doc, geo, onOpenDomain, onReview, reviewable }: {
  ontologyId: string;
  /** レビューの列を読める OM か（API の一覧の値）。読めない OM（正解 OM など）はレビュー待ちを出さず、そう伝える */
  reviewable: boolean | null;
  doc: OntologyDocument;
  geo: readonly GeoColumn[];
  onOpenDomain: (place: Place) => void;
  onReview: () => void;
}) {
  const [index, setIndex] = useState<Load<DomainIndex>>({ state: "loading" });
  const [events, setEvents] = useState<Load<{ summary: DomainSummary; rows: DomainRows }[]>>({ state: "loading" });
  const [review, setReview] = useState<Load<ReviewQueue>>({ state: "loading" });

  useEffect(() => {
    let live = true;
    setIndex({ state: "loading" });
    setEvents({ state: "loading" });
    getDomains(ontologyId).then((value) => {
      if (!live) return;
      setIndex({ state: "ok", value });
      // 出来事のドメインの集合（全体）を読み、時系列のあるものを先に、その中で件数の多い順に（見せ方と集計は API の GroupProfile）。
      // 読めなかったら出来事の欄だけを失敗にする（ドメインの一覧は読めている）
      const events = value.domains.filter((d) => d.kind === "event");
      Promise.all(events.map((summary) => getDomainRows(ontologyId, summary.table, "").then((rows) => ({ summary, rows })))).then(
        (loaded) => { if (live) setEvents({ state: "ok", value: orderEvents(loaded) }); },
        (error: unknown) => { if (live) setEvents({ state: "error", error }); });
    }, (error: unknown) => { if (live) { setIndex({ state: "error", error }); setEvents({ state: "error", error }); } });
    if (reviewable === true) {
      setReview({ state: "loading" });
      getReview(ontologyId).then((value) => { if (live) setReview({ state: "ok", value }); },
                                 (error: unknown) => { if (live) setReview({ state: "error", error }); });
    }
    return () => { live = false; };
  }, [ontologyId, reviewable]);

  const pal = index.state === "ok" ? palette(index.value) : null;
  const hero = events.state === "ok" ? events.value[0]?.rows : undefined;
  const target = geo[0];
  return (
    <div className="ops">
      <aside className="ops-left" aria-label="出来事">
        {events.state === "loading" && <p className="dim">loading…</p>}
        {events.state === "error" && <ErrorText error={events.error} />}
        {events.state === "ok" && events.value.length === 0 && <p className="dim">出来事（event）のドメインが無い</p>}
        {events.state === "ok" && pal !== null && events.value.slice(0, EVENT_CARDS).map(({ summary, rows }) => (
          <EventCard key={summary.table} rows={rows} color={pal.color(summary.table)} kind={summary.kind}
                     onOpen={() => onOpenDomain({ table: summary.table, key: null })} />
        ))}
        <div className="ops-health">
          <div className="eyebrow">OM</div>
          <OmFacts doc={doc} index={index} />
        </div>
      </aside>

      <section className="ops-center" aria-label="地図と時系列">
        <div className="ops-map">
          {target === undefined ? <p className="dim pad">この OM には地図に出せる列（geo.point）が無い</p> : (
            <Suspense fallback={<p className="dim pad">loading map…</p>}>
              <MapView ontologyId={ontologyId} target={target} onOpenDomain={onOpenDomain} />
            </Suspense>
          )}
        </div>
        <div className="ops-bottom">
          <div className="ops-series">
            {hero === undefined ? <p className="dim">出来事の時系列が無い</p> : (
              <>
                <div className="ops-title">
                  <span className="heading">{shortName(hero.name)}</span>
                  <span className="tag">event · {hero.time_column ?? "時刻なし"} · 件数</span>
                </div>
                {hero.series !== undefined && pal !== null
                  ? <Series group={hero} color={pal.color(hero.table)} chartId={`ops-${hero.table}`} height={150} />
                  : <p className="dim">時系列にする出来事の時刻が無い（{hero.view}）</p>}
              </>
            )}
          </div>
          <div className="ops-breakdown">
            {hero !== undefined && (
              <>
                <div className="ops-title"><span className="heading">内訳 · {hero.breakdown_column ?? "—"}</span>
                  <span className="dim small">{shortName(hero.name)}</span></div>
                {(hero.breakdown ?? []).length === 0 ? <p className="dim">内訳の列（enum）が無い</p> : (
                  <ul className="hbars">
                    {(hero.breakdown ?? []).map((b, i) => (
                      <li key={`${b.value ?? ""}#${i}`}>
                        <span className="name">{b.value ?? "—"}</span>
                        <span className="track"><span style={{ width: `${(b.count / Math.max(1, hero.count)) * 100}%` }} /></span>
                        <span className="n">{count.format(b.count)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <Totals group={hero} breakdown={false} />
              </>
            )}
          </div>
        </div>
      </section>

      <aside className="ops-right" aria-label="レビュー待ち">
        <div className="ops-title"><span className="heading big">レビュー待ち</span>
          {review.state === "ok" && <Writer queue={review.value} />}
        </div>
        {reviewable === false && <p className="dim">この OM はレビューしない（レビューするのは作業領域の OM だけ）</p>}
        {reviewable === null && <p className="failure">この OM は OM の一覧に無い（レビューできるか分からない）</p>}
        {reviewable === true && review.state === "loading" && <p className="dim">loading…</p>}
        {reviewable === true && review.state === "error" && <ErrorText error={review.error} />}
        {reviewable === true && review.state === "ok" && <ReviewPulse queue={review.value} onReview={onReview} />}
      </aside>
    </div>
  );
}

/** 出来事のドメインのカード：全体の件数、最新の区切りの件数と前との増減（途中の区切りとは比べない）、時系列の小さな棒。 */
function EventCard({ rows, color, kind, onOpen }: { rows: DomainRows; color: string; kind: string; onOpen: () => void }) {
  const points = rows.series ?? [];
  const t = trend(points);
  const partial = t !== null && partialBucket(t.last.start, rows.series_unit, rows.latest?.day);
  const bars = sparkBars(points, 24);
  return (
    <button className="ops-card blueprint" onClick={onOpen} title="ドメインの集合を開く">
      <Corners />
      <span className="ops-card-head">
        <span className="eyebrow"><DomainIcon kind={kind} color={color} /> {shortName(rows.name)}</span>
        {t !== null && !partial && t.ratio !== undefined && (
          <span className={`kpi-delta ${t.ratio >= 0 ? "up" : "down"}`}>{t.ratio >= 0 ? "▲" : "▼"}{Math.abs(t.ratio * 100).toFixed(0)}%</span>
        )}
        {partial && <span className="dim small">途中</span>}
      </span>
      <span className="ops-card-value">{count.format(rows.count)}</span>
      {bars.length > 0 && (
        <span className="spark">
          {bars.map((h, i) => h === 0 // 0 件の区切りは棒にしない（空の印。小さな棒と見分ける）
            ? <span key={i} className="zero" title="0 件" />
            : <span key={i} style={{ height: `${h}%`, background: i >= bars.length - 3 ? `var(${color})` : undefined }} />)}
        </span>
      )}
      <span className="ops-card-src">
        {t === null ? `${rows.view} · 件数` : `最新の区切り ${bucketLabel(t.last.start, rows.series_unit)} · ${count.format(t.last.count)} 件`}
        {rows.time_column !== undefined && ` · ${rows.time_column}`}
      </span>
    </button>
  );
}

/** 誰として答えるか。書けるのに reviewer が無ければ契約の誤りとして出す（黙って空にしない）。 */
function Writer({ queue }: { queue: ReviewQueue }) {
  if (!queue.writable) return <span className="dim">読むだけ</span>;
  if (queue.reviewer === undefined) return <span className="failure small">書けるのに reviewer が無い（API の応答の誤り）</span>;
  return <span className="dim">{queue.reviewer} として答える</span>;
}

function OmFacts({ doc, index }: { doc: OntologyDocument; index: Load<DomainIndex> }) {
  const approved = doc.tables.filter((t) => t.status === "approved").length;
  const relations = doc.tables.reduce((n, t) => n + (t.relations?.length ?? 0), 0);
  return (
    <dl className="ops-facts">
      <dt>表</dt><dd>{count.format(doc.tables.length)} <i>· 承認済み {count.format(approved)}</i></dd>
      <dt>関係</dt><dd>{count.format(relations)}</dd>
      <dt>ドメイン</dt><dd>{index.state === "ok" ? <>{index.value.domains.length} <i>· ドメインにしない表 {index.value.hidden.length}</i></> : "—"}</dd>
      <dt>ソース</dt><dd>{index.state === "ok" ? (index.value.readable ? "接続済み" : <span className="warn">未接続</span>) : "—"}</dd>
    </dl>
  );
}

/** レビュー待ち：未決の数、対象ごとの数、影響の大きい問い（API の並び）の先頭。 */
function ReviewPulse({ queue, onReview }: { queue: ReviewQueue; onReview: () => void }) {
  const counts = countBySubject(queue.questions);
  const top = queue.questions.slice(0, 8);
  return (
    <>
      <div className="ops-pending"><span className="big-number">{count.format(queue.pending)}</span>
        <span className="dim">件 · 一括承認の候補 {count.format(queue.questions.filter((q) => q.bulk_candidate).length)}</span></div>
      <div className="chips">
        {SUBJECTS.filter((s) => counts[s] > 0).map((s) => <span key={s} className={`tag tag-${s}`}>{SUBJECT_LABEL[s]} {counts[s]}</span>)}
      </div>
      <ol className="ops-queue">
        {top.map((q) => (
          <li key={q.location}>
            <span className={`tag tag-${q.subject}`}>{SUBJECT_LABEL[asSubject(q.subject)]}</span>
            <span className="text">{q.text}</span>
            <span className="conf-n">{q.confidence === undefined ? "—" : q.confidence.toFixed(2)}</span>
          </li>
        ))}
        {top.length === 0 && <li className="dim empty">未決の提案は無い</li>}
      </ol>
      {queue.pending > 0 && (
        <button className="primary blueprint ops-review" onClick={onReview}><Corners />レビューを開く →</button>
      )}
    </>
  );
}
