// レビュー画面の純粋な部分：問いの絞り込み、キーと答えの対応、答えた後に次に出す問い。
// 問いの並び（影響の大きい順）と一括承認の候補は API（Python の Domain）が決める。ここでは並べ替えない。
import { ReviewDecision, type ReviewAnswer, type ReviewQuestion, ReviewSubject, TableKind } from "./om/model";

export type Subject = ReviewSubject;

/** 対象のラベル。①から生成した enum で網羅を型で強制する（対象が増えれば、ここが型エラーになる）。 */
export const SUBJECT_LABEL: Record<ReviewSubject, string> = {
  [ReviewSubject.table]: "表", [ReviewSubject.relation]: "関係", [ReviewSubject.edge]: "辺",
  [ReviewSubject.column]: "列", [ReviewSubject.enum_value]: "値",
};

export const SUBJECTS: readonly ReviewSubject[] = Object.values(ReviewSubject);

/** 契約（JSON Schema の enum）で検証済みの値を、生成された enum として読む（生成型では string になっているため）。 */
export function asSubject(value: string): ReviewSubject {
  if (!(value in SUBJECT_LABEL)) throw new Error(`問いの対象が語彙に無い: ${value}`);
  return value as ReviewSubject;
}

/** 種類を選ぶキー（CLI の mandara review と同じ）。 */
export const KIND_KEYS: Record<string, TableKind> = {
  d: TableKind.domain, r: TableKind.relation, e: TableKind.event,
};

export type KeyAction = { type: "answer"; answer: ReviewAnswer } | { type: "skip" } | { type: "move"; by: number };

/** キーを、問いへの操作にする。その問いに使えないキーは null（種類の要る表に y、など）。 */
export function keyAction(key: string, question: ReviewQuestion): KeyAction | null {
  const k = key.toLowerCase();
  if (k === "j" || k === "arrowdown") return { type: "move", by: 1 };
  if (k === "k" || k === "arrowup") return { type: "move", by: -1 };
  if (k === "s") return { type: "skip" };
  if (k === "n") return { type: "answer", answer: { location: question.location, decision: ReviewDecision.reject } };
  if (!question.needs_kind && k === "y") {
    return { type: "answer", answer: { location: question.location, decision: ReviewDecision.approve } };
  }
  const kind = KIND_KEYS[k];
  if (question.needs_kind && kind !== undefined) {
    return { type: "answer", answer: { location: question.location, decision: ReviewDecision.approve, kind } };
  }
  return null;
}

export function visible(questions: readonly ReviewQuestion[], subject: Subject | null): ReviewQuestion[] {
  return subject === null ? [...questions] : questions.filter((q) => q.subject === subject);
}

export function countBySubject(questions: readonly ReviewQuestion[]): Record<Subject, number> {
  const counts = Object.fromEntries(SUBJECTS.map((s) => [s, 0])) as Record<Subject, number>;
  for (const q of questions) counts[asSubject(q.subject)] += 1;
  return counts;
}

/** 次に出す問い：答えた（または飛ばした）問いの位置から先で、飛ばしていない最初の問い。無ければ先頭から探す。
 *  answered は答える前の列での位置を知るために、答える前の列（before）で探す。 */
export function nextLocation(before: readonly ReviewQuestion[], after: readonly ReviewQuestion[], answered: string,
                             skipped: ReadonlySet<string>): string | null {
  const start = before.findIndex((q) => q.location === answered);
  const remaining = new Set(after.map((q) => q.location));
  const candidates = [...before.slice(start + 1), ...before.slice(0, Math.max(start, 0)), ...after];
  return candidates.find((q) => q.location !== answered && remaining.has(q.location) && !skipped.has(q.location))
    ?.location ?? null;
}
