// toGraph の法則。入力は Python（Hypothesis）が生成した文書 40 件と正解 OM（tests/contract_samples.py）。
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseDocument } from "./api";
import { GraphError, toGraph } from "./graph";
import type { OntologyDocument } from "./om/model";

const repo = fileURLToPath(new URL("../..", import.meta.url));
const samples = JSON.parse(
  execFileSync("uv", ["run", "python", "-m", "tests.contract_samples"], { cwd: repo, encoding: "utf-8" }),
) as { documents: unknown[]; gold: unknown };
const docs = [...samples.documents, samples.gold].map(parseDocument);

describe("toGraph の法則（生成した文書すべて）", () => {
  it("表ごとにノード1つ、関係ごと・辺ごとにエッジ1つ。ID は一意、端点は実在する", () => {
    for (const doc of docs) {
      const { nodes, edges } = toGraph(doc);
      expect(nodes.map((n) => n.id)).toEqual(doc.tables.map((t) => t.id));
      const count = doc.tables.reduce((n, t) => n + (t.relations?.length ?? 0) + (t.edge ? 1 : 0), 0);
      expect(edges).toHaveLength(count);
      const ids = [...nodes.map((n) => n.id), ...edges.map((e) => e.id)];
      expect(new Set(ids).size).toBe(ids.length); // Cytoscape は ID の重複で落ちる
      const nodeIds = new Set(nodes.map((n) => n.id));
      for (const e of edges) expect(nodeIds.has(e.source) && nodeIds.has(e.target)).toBe(true);
    }
  });

  it("関係のエッジは、型・状態を保ち、self のときだけ自分に戻る", () => {
    for (const doc of docs) {
      const edges = new Map(toGraph(doc).edges.map((e) => [e.id, e]));
      for (const table of doc.tables) {
        for (const r of table.relations ?? []) {
          const e = edges.get(`${table.id}|relation|${r.id}`);
          expect(e).toBeDefined();
          expect([e!.type, e!.status, e!.via, e!.source]).toEqual([r.type, r.status, "relation", table.id]);
          expect(e!.target === table.id).toBe(r.target === "self" || r.target === table.id);
        }
      }
    }
  });

  it("辺のエッジは、from の参照先から to の参照先へ向かう", () => {
    for (const doc of docs) {
      const edges = new Map(toGraph(doc).edges.map((e) => [e.id, e]));
      for (const table of doc.tables.filter((t) => t.edge !== undefined)) {
        const relations = new Map((table.relations ?? []).map((r) => [r.id, r]));
        const end = (id: string) => {
          const target = relations.get(id)!.target;
          return target === "self" ? table.id : target;
        };
        const e = edges.get(`${table.id}|edge`)!;
        expect([e.source, e.target, e.type, e.via]).toEqual([end(table.edge!.from), end(table.edge!.to), table.edge!.type, "edge"]);
      }
    }
  });
});

describe("toGraph の例（正解 OM）", () => {
  const gold = parseDocument(samples.gold);

  it("部品表の辺は製品から製品へ、従業員の上司は自己参照", () => {
    const { edges } = toGraph(gold);
    const bom = edges.find((e) => e.via === "edge");
    expect([bom?.type, bom?.source === bom?.target, bom?.label]).toEqual(["part-of", true, "billofmaterials"]);
    const reportsTo = edges.find((e) => e.label === "fn_hierarchy_path_parent");
    expect(reportsTo?.source).toBe(reportsTo?.target);
  });

  it("参照先が無い関係、無い関係を指す辺は、隠さずにエラー", () => {
    const missingTarget = structuredClone(gold) as OntologyDocument;
    missingTarget.tables.find((t) => (t.relations?.length ?? 0) > 0)!.relations![0]!.target = "tbl_nowhere";
    expect(() => toGraph(missingTarget)).toThrow(GraphError);

    const brokenEdge = structuredClone(gold) as OntologyDocument;
    brokenEdge.tables.find((t) => t.edge !== undefined)!.edge!.from = "rel_nowhere";
    expect(() => toGraph(brokenEdge)).toThrow(/無い関係/);
  });
});
