// mandara serve（Python）との境界。別言語の出力は信頼できない入力として、①から生成した JSON Schema で検証してから
// 型として扱う。検証器は厳格（strict）にし、日付などの format も検証する。
import Ajv2019 from "ajv/dist/2019";
import addFormats from "ajv-formats";
import indexSchema from "./om/index.schema.json";
import type { OntologyDocument, OntologyIndex, PointSet } from "./om/model";
import documentSchema from "./om/om.schema.json";
import pointsSchema from "./om/points.schema.json";

export class ApiError extends Error {
  constructor(message: string, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

function validator<T>(schema: object, what: string): (data: unknown) => T {
  // 生成したスキーマはどれも同じ $id（①）を持つので、スキーマごとに検証器を分ける
  const ajv = new Ajv2019({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addVocabulary(["metamodel_version", "version"]); // LinkML が書く注記のキーワード（検証には使わない）
  const validate = ajv.compile<T>(schema);
  return (data) => {
    if (!validate(data)) {
      const errors = (validate.errors ?? []).slice(0, 5).map((e) => `${e.instancePath} ${e.message}`);
      throw new ApiError(`${what}が契約に合わない: ${errors.join("; ")}`, validate.errors);
    }
    return data;
  };
}

/** OM の文書を検証する。通らなければ ApiError（Ajv のエラー全件を detail に持つ）。 */
export const parseDocument = validator<OntologyDocument>(documentSchema, "OM の文書");
/** 見られる OM の一覧を検証する。 */
export const parseIndex = validator<OntologyIndex>(indexSchema, "OM の一覧");
/** 地図の点を検証する。 */
export const parsePoints = validator<PointSet>(pointsSchema, "地図の点");

async function getJson(path: string): Promise<unknown> {
  const response = await fetch(path);
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch (cause) {
    throw new ApiError(`${path} の応答が JSON ではない（${response.status}）`, { text: text.slice(0, 500), cause });
  }
  if (!response.ok) {
    const message = typeof body === "object" && body !== null && "error" in body ? String(body.error) : "（理由なし）";
    throw new ApiError(`${response.status} ${message}`, body);
  }
  return body;
}

export async function listOntologies(): Promise<OntologyIndex> {
  return parseIndex(await getJson("/api/ontologies"));
}

export async function getOntology(id: string): Promise<OntologyDocument> {
  return parseDocument(await getJson(`/api/ontologies/${encodeURIComponent(id)}`));
}

export async function getPoints(ontologyId: string, tableId: string, column: string): Promise<PointSet> {
  const path = `/api/ontologies/${encodeURIComponent(ontologyId)}/tables/${encodeURIComponent(tableId)}/points`;
  return parsePoints(await getJson(`${path}?column=${encodeURIComponent(column)}`));
}
