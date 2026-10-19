// ドメインビューワの純粋な部分の性質：たどった道は同じ場所に戻ると切れ、長さは上限まで。
import { describe, expect, it } from "vitest";
import { emphasized, extendTrail, groupKey, kpisOf, linkTexts, objectsOf, orderEvents, type Place, relatedTitle, rowKey, samePlace, TRAIL_LIMIT } from "./domain";

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

describe("groupKey / relatedTitle", () => {
  const base = { kind: "via", relation: "rel_um", relation_type: "has", table: "t_um", name: "production.unitmeasure",
                 columns: ["unitmeasurecode"], via_table: "t_bom", via_name: "production.billofmaterials", rows: [],
                 truncated: false, view: "list", count: 1 };
  it("同じ relation の表を別の側から見た集まりは、鍵も見出しも別", () => {
    const a = { ...base, via_relation: "rel_assembly", via_columns: ["productassemblyid"] };
    const b = { ...base, via_relation: "rel_component", via_columns: ["componentid"] };
    expect(groupKey(a)).not.toBe(groupKey(b));
    expect(relatedTitle(a, [a, b]).detail).toBe("productassemblyid から");  // 同じ表を2つの側から見るときだけ
    expect(relatedTitle(b, [a, b]).detail).toBe("componentid から");
    expect(relatedTitle(a, [a]).detail).toBe("");  // relation の表はリンク。名前も列も出さない
    expect(JSON.stringify(relatedTitle(a, [a, b]))).not.toContain("billofmaterials");
  });
  it("つながりの値は先の行ごとにまとめ、2本なら並べる。NULL の列と空の行は出さない", () => {
    const row = (key: string, cells: [string, string | undefined][]) => ({
      table: "t", key: [key], label: key, label_source: "own", cells: [],
      link_cells: cells.map(([column, value]) => ({ column, ...(value === undefined ? {} : { value }) })) });
    const links = { table: "m", name: "m", rows: [row("1", [["startdate", "2020-01-01"], ["enddate", undefined]]),
                    row("1", [["startdate", "2022-01-01"]]), row("2", [["enddate", undefined]])],
                    truncated: false, missing: 0, unlinked: 0, view: "list", count: 3 };
    const texts = linkTexts(links as never);
    expect(texts.get(rowKey(["1"]))).toBe("startdate 2020-01-01 / startdate 2022-01-01");
    expect(texts.has(rowKey(["2"]))).toBe(false);
    expect(linkTexts(undefined).size).toBe(0);
  });
});

describe("within", () => {
  it("同じ表の集合でも、絞る親が違えば別の場所", () => {
    const all: Place = { table: "t", key: null };
    const mine: Place = { table: "t", key: null, within: { table: "p", key: ["1"], relation: "r" } };
    const yours: Place = { table: "t", key: null, within: { table: "p", key: ["2"], relation: "r" } };
    expect(samePlace(all, mine)).toBe(false);
    expect(samePlace(mine, yours)).toBe(false);
    expect(samePlace(mine, { ...mine })).toBe(true);
    expect(extendTrail([all, mine], yours)).toEqual([all, mine, yours]);
  });
});

describe("kpisOf", () => {
  const group = (name: string, count: number, measures?: unknown[]) => ({
    kind: "children", relation: `rel_${name}`, relation_type: "has", table: `t_${name}`, name: `s.${name}`, columns: ["x"],
    rows: [], truncated: false, view: "summary", count, ...(measures === undefined ? {} : { measures }) });
  it("先頭から上限まで、件数はそのまま、合計の尺度は1つ目だけ（平均は使わない）", () => {
    const groups = [group("a", 10, [{ column: "q", op: "avg", value: "2" }, { column: "amt", op: "sum", value: "99", unit_text: "USD" }]),
                    group("b", 3), group("c", 1), group("d", 7), group("e", 5)];
    const k = kpisOf(groups as never, 4);
    expect(k.map((x) => [x.label, x.count])).toEqual([["a", 10], ["b", 3], ["c", 1], ["d", 7]]);
    expect(k[0]?.measure).toEqual({ column: "amt", value: "99", unit: "USD" });
    expect(k[1]?.measure).toBeUndefined();
  });
});

describe("objectsOf", () => {
  const index = { ontology: "o", readable: true, entity_groups: ["s"],
                  domains: [{ table: "t1" }], hidden: [{ table: "t2", name: "s.t2", reason: "主キーが無い" }] };
  it("ドメインなら開ける、隠した表は理由、どちらでもなければ none、両方なら誤り", () => {
    expect(objectsOf(index as never, "t1")).toEqual({ state: "domain" });
    expect(objectsOf(index as never, "t2")).toEqual({ state: "hidden", reason: "主キーが無い" });
    expect(objectsOf(index as never, "t3")).toEqual({ state: "none" });
    const both = { ...index, domains: [{ table: "t2" }] };
    expect(() => objectsOf(both as never, "t2")).toThrow("両方");
  });
});

describe("orderEvents", () => {
  const ev = (table: string, count: number, timed: boolean) => ({ rows: { table, count, ...(timed ? { series: [] } : {}) } });
  it("時系列のあるものが常に先、その中で件数の多い順。集合は変わらない", () => {
    let s = 3;
    const rnd = () => { s = (s * 48271) % 2147483647; return s; };
    for (let k = 0; k < 20; k++) {
      const input = Array.from({ length: 8 }, (_, i) => ev(`t${i}`, rnd() % 50, rnd() % 2 === 0));
      const out = orderEvents(input);
      expect(new Set(out)).toEqual(new Set(input));
      const firstUntimed = out.findIndex((e) => e.rows.series === undefined);
      if (firstUntimed >= 0) expect(out.slice(firstUntimed).every((e) => e.rows.series === undefined)).toBe(true);
      for (let i = 1; i < out.length; i++) {
        const [a, b] = [out[i - 1], out[i]];
        if (a !== undefined && b !== undefined && (a.rows.series === undefined) === (b.rows.series === undefined)) {
          expect(a.rows.count).toBeGreaterThanOrEqual(b.rows.count);
        }
      }
    }
  });
});
