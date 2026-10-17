// ドメインビューワの純粋な部分の性質：たどった道は同じ場所に戻ると切れ、長さは上限まで。
import { describe, expect, it } from "vitest";
import { emphasized, extendTrail, type Place, samePlace, TRAIL_LIMIT } from "./domain";

const p = (table: string, key: string[] | null = null): Place => ({ table, key });

describe("たどった道", () => {
  it("足すと末尾に付き、同じ場所に戻るとそこまでに切れる", () => {
    const trail = [p("a"), p("a", ["1"]), p("b", ["2"])];
    expect(extendTrail(trail, p("c", ["3"]))).toEqual([...trail, p("c", ["3"])]);
    expect(extendTrail(trail, p("a", ["1"]))).toEqual([p("a"), p("a", ["1"])]);
  });
  it("集合と個は別の場所、キーが違えば別の場所", () => {
    expect(samePlace(p("a"), p("a", []))).toBe(false);
    expect(samePlace(p("a", ["1", "2"]), p("a", ["12"]))).toBe(false);
    expect(samePlace(p("a", ["1"]), p("a", ["1"]))).toBe(true);
  });
  it("長さは上限まで（古い方から落ちる）", () => {
    let trail: Place[] = [];
    for (let i = 0; i < TRAIL_LIMIT + 5; i++) trail = extendTrail(trail, p("t", [String(i)]));
    expect(trail).toHaveLength(TRAIL_LIMIT);
    expect(trail.at(-1)).toEqual(p("t", [String(TRAIL_LIMIT + 4)]));
  });
});

describe("emphasized", () => {
  const domain = { key_columns: ["id"], label_columns: ["name"], natural_keys: ["code"] };
  it("主キー、見出し、自然キーの列だけを強調する", () => {
    expect(["id", "name", "code", "price"].filter((c) => emphasized(domain, c))).toEqual(["id", "name", "code"]);
  });
  it("識別の列が無いドメインでは何も強調しない", () => {
    const none = { key_columns: [], label_columns: [], natural_keys: [] };
    expect(["id", "name"].some((c) => emphasized(none, c))).toBe(false);
  });
});
