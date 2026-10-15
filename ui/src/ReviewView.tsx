// レビュー画面（仕様 9.3）：提案に問いの形で答える。根拠は3行まで、影響の大きい順、一括承認は一覧を見てから。
// 書き込みは API（CLI と同じユースケース）だけが行う。当てられない答えは API の理由をそのまま見せ、選び直してもらう。
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getReview, postAnswer, postBulk, WrittenButStaleError } from "./api";
import type { OntologyDocument, ReviewAnswer, ReviewQuestion, ReviewQueue } from "./om/model";
import { KIND_KEYS, SUBJECT_LABEL, SUBJECTS, asSubject, countBySubject, keyAction, nextLocation, visible,
         type Subject } from "./review";
import { TableDetail } from "./TableDetail";
import { KIND } from "./vocabulary";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

export function ReviewView({ ontologyId, doc, onChanged, onOpenTable }: {
  ontologyId: string;
  doc: OntologyDocument;
  /** 答えを書いた後に呼ぶ（OM を読み直す）。 */
  onChanged: () => void;
  onOpenTable: (tableId: string) => void;
}) {
  const [queue, setQueue] = useState<Load<ReviewQueue>>({ state: "loading" });
  const [subject, setSubject] = useState<Subject | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<ReadonlySet<string>>(new Set());
  const [answered, setAnswered] = useState(0);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  useEffect(() => {
    setQueue({ state: "loading" });
    getReview(ontologyId).then(
      (value) => { setQueue({ state: "ok", value }); setCurrent(value.questions[0]?.location ?? null); },
      (error: unknown) => setQueue({ state: "error", error }),
    );
  }, [ontologyId]);

  const questions = queue.state === "ok" ? queue.value.questions : [];
  const shown = useMemo(() => visible(questions, subject), [questions, subject]);
  const focus = shown.find((q) => q.location === current) ?? shown[0];

  const answer = useCallback(async (a: ReviewAnswer) => {
    if (queue.state !== "ok" || busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      const after = await postAnswer(ontologyId, a);
      setCurrent(nextLocation(shown, visible(after.questions, subject), a.location, skipped));
      setQueue({ state: "ok", value: after });
      setAnswered((n) => n + 1);
      onChanged();
    } catch (error) {
      if (error instanceof WrittenButStaleError) {
        // 答えは書かれた。古い問いの列で答え直させないよう、列を失敗として出す（読み直しは画面を開き直す）
        setQueue({ state: "error", error });
        onChanged();
      } else {
        setRefusal(error instanceof ApiError ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  }, [queue, busy, ontologyId, shown, subject, skipped, onChanged]);

  const skip = useCallback((q: ReviewQuestion) => {
    const now = new Set(skipped).add(q.location);
    setSkipped(now);
    setRefusal(null);
    setCurrent(nextLocation(shown, shown, q.location, now));
  }, [shown, skipped]);

  const move = useCallback((by: number) => {
    if (focus === undefined) return;
    const i = shown.indexOf(focus);
    const next = shown[Math.min(Math.max(i + by, 0), shown.length - 1)];
    if (next !== undefined) { setCurrent(next.location); setRefusal(null); }
  }, [focus, shown]);

  useEffect(() => {
    if (queue.state !== "ok" || focus === undefined || bulkOpen) return;
    const writable = queue.value.writable;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.target instanceof HTMLSelectElement) return;
      const action = keyAction(e.key, focus);
      if (action === null) return;
      e.preventDefault();
      if (action.type === "move") move(action.by);
      else if (action.type === "skip") skip(focus);
      else if (writable) void answer(action.answer);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [queue, focus, bulkOpen, move, skip, answer]);

  if (queue.state === "loading") return <p className="dim pad">loading review…</p>;
  if (queue.state === "error") {
    const message = queue.error instanceof ApiError ? queue.error.message : String(queue.error);
    return <p className="failure pad">{message}</p>;
  }
  const q = queue.value;
  const counts = countBySubject(q.questions);
  const candidates = q.questions.filter((x) => x.bulk_candidate);
  const table = focus?.table === undefined ? undefined : doc.tables.find((t) => t.id === focus.table);

  return (
    <div className="review">
      <aside className="review-queue" aria-label="問いの列">
        <header>
          <span className="label">QUEUE</span>
          <span className="count">{q.pending.toLocaleString()}</span>
          <span className="dim">pending</span>
        </header>
        <div className="chips" role="group" aria-label="対象で絞る">
          <button className={subject === null ? "on" : ""} aria-pressed={subject === null}
                  onClick={() => setSubject(null)}>all {q.pending}</button>
          {SUBJECTS.filter((s) => counts[s] > 0).map((s) => (
            <button key={s} className={subject === s ? "on" : ""} aria-pressed={subject === s}
                    onClick={() => setSubject(s)}>{SUBJECT_LABEL[s]} {counts[s]}</button>
          ))}
        </div>
        <ol className="queue-list">
          {shown.map((x) => (
            <li key={x.location}>
              <button className={`queue-item${x === focus ? " on" : ""}${skipped.has(x.location) ? " skipped" : ""}`}
                      onClick={() => { setCurrent(x.location); setRefusal(null); }}>
                <span className={`tag tag-${x.subject}`}>{SUBJECT_LABEL[asSubject(x.subject)]}</span>
                <span className="text">{x.text}</span>
                <Confidence value={x.confidence} high={q.bulk_minimum} compact />
              </button>
            </li>
          ))}
        </ol>
      </aside>

      <section className="review-focus">
        {!q.writable && <p className="banner warn">読むだけ：{q.not_writable_reason}</p>}
        {q.writable && candidates.length > 0 && (
          <div className="banner bulk">
            <span>一括承認の候補 <strong>{candidates.length.toLocaleString()}</strong> 件（確信度 ≥ {q.bulk_minimum}）</span>
            <button onClick={() => setBulkOpen(true)}>一覧を見る</button>
          </div>
        )}
        {bulkOpen ? (
          <BulkPanel ontologyId={ontologyId} queue={q} candidates={candidates}
                     onDone={(after) => { setQueue({ state: "ok", value: after }); setBulkOpen(false);
                                          setCurrent(after.questions[0]?.location ?? null); onChanged(); }}
                     onClose={() => setBulkOpen(false)} />
        ) : focus === undefined ? (
          <div className="done">
            <div className="done-mark">◆</div>
            <p>{q.pending === 0 ? "すべての提案に答えた" : "この絞り込みの問いは無い"}</p>
            <p className="dim">この画面で {answered} 件に答えた</p>
          </div>
        ) : (
          <QuestionCard question={focus} index={shown.indexOf(focus)} total={shown.length} writable={q.writable}
                        bulkMinimum={q.bulk_minimum}
                        reviewer={q.reviewer} busy={busy} refusal={refusal} onAnswer={(a) => void answer(a)}
                        onSkip={() => skip(focus)}
                        onOpenTable={focus.table === undefined ? undefined : () => onOpenTable(focus.table ?? "")} />
        )}
      </section>

      {table !== undefined && !bulkOpen && <TableDetail doc={doc} table={table} onSelect={onOpenTable} />}
    </div>
  );
}

function QuestionCard({ question, index, total, writable, bulkMinimum, reviewer, busy, refusal, onAnswer, onSkip,
                        onOpenTable }: {
  question: ReviewQuestion;
  index: number;
  total: number;
  writable: boolean;
  bulkMinimum: number;
  reviewer: string | undefined;
  busy: boolean;
  refusal: string | null;
  onAnswer: (a: ReviewAnswer) => void;
  onSkip: () => void;
  onOpenTable: (() => void) | undefined;
}) {
  const subject = asSubject(question.subject);
  const hidden = question.evidence_total - question.evidence.length;
  const approve = (kind?: ReviewAnswer["kind"]) => onAnswer(
    kind === undefined ? { location: question.location, decision: "approve" }
                       : { location: question.location, decision: "approve", kind });
  return (
    <article className="card" aria-label="問い">
      <div className="card-head">
        <span className={`tag tag-${subject}`}>{SUBJECT_LABEL[subject]}</span>
        <span className="dim">{index + 1} / {total}</span>
        <span className="spacer" />
        {onOpenTable !== undefined && <button className="link" onClick={onOpenTable}>表をグラフで見る →</button>}
      </div>
      <h1 className="question">{question.text}</h1>
      <Confidence value={question.confidence} high={bulkMinimum} />
      <ul className="evidence" aria-label="根拠">
        {question.evidence.map((e, i) => (
          <li key={e.id}>
            <span className="branch">{i === question.evidence.length - 1 && hidden === 0 ? "└" : "├"}</span>
            <span>{e.text}</span>
            <span className="dim id">{e.id}</span>
          </li>
        ))}
        {hidden > 0 && <li className="dim"><span className="branch">└</span>ほか {hidden} 件の根拠</li>}
        {question.evidence_total === 0 && <li className="dim">根拠なし</li>}
      </ul>
      <div className="actions">
        {question.needs_kind ? (
          Object.entries(KIND_KEYS).map(([key, kind]) => (
            <button key={key} className="approve" disabled={!writable || busy} onClick={() => approve(kind)}>
              <kbd>{key.toUpperCase()}</kbd> {KIND[kind].text}
            </button>
          ))
        ) : (
          <button className="approve" disabled={!writable || busy} onClick={() => approve()}>
            <kbd>Y</kbd> はい（承認）
          </button>
        )}
        <button className="reject" disabled={!writable || busy}
                onClick={() => onAnswer({ location: question.location, decision: "reject" })}>
          <kbd>N</kbd> いいえ（却下）
        </button>
        <button onClick={onSkip}><kbd>S</kbd> 飛ばす</button>
      </div>
      {refusal !== null && <p className="failure refusal">{refusal}</p>}
      <p className="dim small keys">
        J / K で移動{writable && reviewer !== undefined ? ` · ${reviewer} として書く` : ""}
      </p>
    </article>
  );
}

function BulkPanel({ ontologyId, queue, candidates, onDone, onClose }: {
  ontologyId: string;
  queue: ReviewQueue;
  candidates: ReviewQuestion[];
  onDone: (after: ReviewQueue) => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      onDone(await postBulk(ontologyId, { threshold: queue.bulk_minimum, locations: candidates.map((c) => c.location) }));
    } catch (error) {
      if (error instanceof WrittenButStaleError) throw error; // 書いた後の失敗は、一覧の画面で拾わない
      setRefusal(error instanceof ApiError ? error.message : String(error));
      setBusy(false);
    }
  };
  return (
    <section className="bulk-panel" aria-label="一括承認">
      <header>
        <h1>一括承認 <span className="dim">確信度 ≥ {queue.bulk_minimum} · {candidates.length.toLocaleString()} 件</span></h1>
        <button onClick={onClose} disabled={busy}>閉じる</button>
      </header>
      <ol className="bulk-list">
        {candidates.map((c) => (
          <li key={c.location}>
            <Confidence value={c.confidence} high={queue.bulk_minimum} compact />
            <span className={`tag tag-${c.subject}`}>{SUBJECT_LABEL[asSubject(c.subject)]}</span>
            <span>{c.text}</span>
          </li>
        ))}
      </ol>
      <footer>
        {refusal !== null && <p className="failure">{refusal}</p>}
        <button className="approve" disabled={busy || candidates.length === 0} onClick={() => void run()}>
          {busy ? "承認しています…" : `この ${candidates.length.toLocaleString()} 件を承認する`}
        </button>
      </footer>
    </section>
  );
}

/** 確信度。high（一括承認の閾値）以上を緑にする。0.7 は見た目の段の区切りで、判断には使わない。 */
function Confidence({ value, high, compact = false }: { value: number | undefined; high: number; compact?: boolean }) {
  if (value === undefined) return <span className="dim conf">—</span>;
  const level = value >= high ? "high" : value >= 0.7 ? "mid" : "low";
  return (
    <span className={`conf conf-${level}${compact ? " compact" : ""}`} title={`確信度 ${value.toFixed(2)}`}>
      {!compact && <span className="meter"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>}
      {value.toFixed(2)}
    </span>
  );
}
