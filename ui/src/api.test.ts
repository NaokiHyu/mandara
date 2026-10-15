// プロセス境界の契約：Python（mandara）が出す文書を、UI が実際に使う検証器（Ajv + ①から生成した JSON Schema）が
// 受け入れる。Hypothesis で生成した文書すべてと、サーバーと同じ経路で作った正解 OM で確かめる。
// 契約に合わない文書は、理由を添えて拒否する。
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ApiError, parseDocument, parseIndex } from "./api";

const repo = fileURLToPath(new URL("../..", import.meta.url));

interface Samples {
  documents: unknown[];
  gold: unknown;
  gold_tables: number;
  index: unknown;
}

const samples = JSON.parse(
  execFileSync("uv", ["run", "python", "-m", "tests.contract_samples"], { cwd: repo, encoding: "utf-8" }),
) as Samples;

function clone<T>(value: T): T {
  return structuredClone(value);
}

describe("契約：Python の出力を UI の検証器が受け入れる", () => {
  it("Hypothesis で生成した文書すべて", () => {
    expect(samples.documents.length).toBeGreaterThan(0);
    for (const doc of samples.documents) parseDocument(doc);
  });

  it("正解 OM の文書（表の数は Python 側と一致）", () => {
    expect(parseDocument(samples.gold).tables).toHaveLength(samples.gold_tables);
  });

  it("見られる OM の一覧", () => {
    expect(parseIndex(samples.index).ontologies[0]?.kind).toBe("gold");
  });
});

describe("契約：合わない文書は理由を添えて拒否する", () => {
  type Doc = { tables: Record<string, unknown>[]; [key: string]: unknown };
  const broken: [string, (d: Doc) => void, RegExp][] = [
    ["ID の形", (d) => { d.tables[0]!.id = "not-a-table-id"; }, /tbl_/],
    ["必須の欠け", (d) => { delete d.vocabulary; }, /vocabulary/],
    ["閉じた集合の外の値", (d) => { d.tables[0]!.status = "maybe"; }, /allowed values/],
    ["余分な項目", (d) => { d.tables[0]!.surprise = 1; }, /additional/],
    ["日付の書式", (d) => { d.tables[0]!.reviewed_at = "not-a-date"; }, /format "date"/],
  ];
  for (const [name, breakIt, reason] of broken) {
    it(name, () => {
      const doc = clone(samples.gold) as Doc;
      breakIt(doc);
      expect(() => parseDocument(doc)).toThrow(ApiError);
      expect(() => parseDocument(doc)).toThrow(reason);
    });
  }

  it("一覧の閉じた集合の外の値", () => {
    const index = clone(samples.index) as { ontologies: { kind: string }[] };
    index.ontologies[0]!.kind = "somewhere";
    expect(() => parseIndex(index)).toThrow(/allowed values/);
  });
});
