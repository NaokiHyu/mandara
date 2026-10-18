// レビュー画面の純粋な部分の性質：キーの対応は CLI と同じで、使えないキーは何もしない。次に出す問いは、
// 答えた後の列に残っていて、飛ばしていない問いだけ。
import { describe, expect, it } from "vitest";
import { type ReviewQuestion, ReviewSubject } from "./om/model";
import { approveAllBatch, countBySubject, keyAction, nextLocation, tableBatch, visible, confidenceHistogram } from "./review";

function question(location: string, extra: Partial<ReviewQuestion> = {}): ReviewQuestion {
  return { location, subject: "column", text: location, evidence: [], evidence_total: 0, needs_kind: false,
           bulk_candidate: false, ...extra };
}

describe("キーと答え", () => {
  it("種類の要らない問い：y は承認、n は却下、種類のキーは使えない", () => {
    const q = question("a");
    expect(keyAction("y", q)).toEqual({ type: "answer", answer: { location: "a", decision: "approve" } });
    expect(keyAction("N", q)).toEqual({ type: "answer", answer: { location: "a", decision: "reject" } });
    expect(keyAction("d", q)).toBeNull();
  });

  it("種類の要る表：y は使えず、d/r/e/v で種類を選んで承認する", () => {
    const q = question("t", { subject: "table", needs_kind: true });
    expect(keyAction("y", q)).toBeNull();
    expect(keyAction("e", q)).toEqual({ type: "answer", answer: { location: "t", decision: "approve", kind: "event" } });
    expect(keyAction("n", q)).toEqual({ type: "answer", answer: { location: "t", decision: "reject" } });
  });

  it("移動と飛ばす", () => {
    expect(keyAction("j", question("a"))).toEqual({ type: "move", by: 1 });
    expect(keyAction("ArrowUp", question("a"))).toEqual({ type: "move", by: -1 });
    expect(keyAction("s", question("a"))).toEqual({ type: "skip" });
    expect(keyAction("x", question("a"))).toBeNull();
  });
});

describe("次に出す問い", () => {
  const before = ["a", "b", "c", "d"].map((l) => question(l));

  it("答えた問いの次の、残っている問い", () => {
    const after = before.filter((q) => q.location !== "b");
    expect(nextLocation(before, after, "b", new Set())).toBe("c");
  });

  it("飛ばした問いと、もう無い問いは出さない。末尾なら先頭に戻る", () => {
    const after = before.filter((q) => q.location !== "d" && q.location !== "a");
    expect(nextLocation(before, after, "d", new Set(["b"]))).toBe("c");
  });

  it("性質：返す問いは、答えた後の列にあり、飛ばしておらず、答えた問いではない", () => {
    const locations = ["a", "b", "c", "d", "e", "f"];
    for (let mask = 0; mask < 64; mask++) {
      for (const answered of locations) {
        const all = locations.map((l) => question(l));
        const after = all.filter((q, i) => q.location !== answered && (mask & (1 << i)) === 0);
        const skipped = new Set(locations.filter((_, i) => (mask >> 3) & (1 << (i % 3))));
        const next = nextLocation(all, after, answered, skipped);
        const allowed = after.filter((q) => !skipped.has(q.location)).map((q) => q.location);
        if (allowed.length === 0) expect(next).toBeNull();
        else expect(next !== null && allowed.includes(next)).toBe(true);
      }
    }
  });
});

describe("絞り込み", () => {
  it("対象ごとの数と、絞り込んだ列", () => {
    const qs = [question("a"), question("t", { subject: "table" }), question("b")];
    expect(countBySubject(qs)).toEqual({ table: 1, relation: 0, edge: 0, column: 2, enum_value: 0 });
    expect(visible(qs, ReviewSubject.column).map((q) => q.location)).toEqual(["a", "b"]);
    expect(visible(qs, null)).toHaveLength(3);
  });
});

describe("表ごと承認", () => {
  it("その表の、種類を選ばなくてよい問いだけ", () => {
    const qs = [question("a", { table: "t1" }), question("k", { table: "t1", subject: "table", needs_kind: true }),
                question("b", { table: "t2" }), question("v"), question("c", { table: "t1", subject: "relation" })];
    expect(tableBatch(qs, "t1").map((q) => q.location)).toEqual(["a", "c"]);
    expect(tableBatch(qs, "none")).toEqual([]);
    expect(approveAllBatch(qs).map((q) => q.location)).toEqual(["a", "b", "v", "c"]);
  });
});

describe("確信度の分布", () => {
  it("どの問いもちょうど1か所に数える（区間・下・無し）。1.0 は最後の区間、区間の境目は上の区間", () => {
    const values = [undefined, 0.2, 0.5, 0.55, 0.7, 0.95, 0.999, 1.0, 1.0];
    const h = confidenceHistogram(values, 0.5, 10);
    expect(h.counts.reduce((a, b) => a + b, 0) + h.below + h.none).toBe(values.length);
    expect([h.below, h.none]).toEqual([1, 1]);
    expect(h.counts[0]).toBe(1);           // 0.5
    expect(h.counts[1]).toBe(1);           // 0.55
    expect(h.counts[4]).toBe(1);           // 0.7
    expect(h.counts[9]).toBe(4);           // 0.95, 0.999, 1.0, 1.0
  });
  it("擬似乱数の値でも和は数と同じ", () => {
    let s = 7;
    const values = Array.from({ length: 300 }, () => { s = (s * 48271) % 2147483647; return s / 2147483647; });
    const h = confidenceHistogram(values);
    expect(h.counts.reduce((a, b) => a + b, 0) + h.below + h.none).toBe(300);
  });
});
