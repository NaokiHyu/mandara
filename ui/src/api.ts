// mandara serve（Python）との境界。別言語の出力は信頼できない入力として、①から生成した JSON Schema で検証してから
// 型として扱う。検証器は厳格（strict）にし、日付などの format も検証する。
import Ajv2019 from "ajv/dist/2019";
import addFormats from "ajv-formats";
import indexSchema from "./om/index.schema.json";
import type { BulkApproval, OntologyDocument, OntologyIndex, PointSet, ReviewAnswer, ReviewQueue,
              TableApproval } from "./om/model";
import documentSchema from "./om/om.schema.json";
import pointsSchema from "./om/points.schema.json";
import reviewSchema from "./om/review.schema.json";

export class ApiError extends Error {
  constructor(message: string, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

/** 書く要求は通った（答えは書かれた）が、その後の読み直しに失敗した。答えを選び直させてはいけない。 */
export class WrittenButStaleError extends ApiError {
  constructor(message: string, detail?: unknown) {
    super(message, detail);
    this.name = "WrittenButStaleError";
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
/** レビューの問いの列を検証する。 */
export const parseReview = validator<ReviewQueue>(reviewSchema, "レビューの問いの列");

async function getJson(path: string): Promise<unknown> {
  return readJson(path, await fetch(path));
}

/** 書く要求は JSON で送る（API は application/json 以外の書き込みを拒む）。 */
async function postJson(path: string, body: unknown): Promise<unknown> {
  return readJson(path, await fetch(path, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  }));
}

async function readJson(path: string, response: Response): Promise<unknown> {
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch (cause) {
    throw new ApiError(`${path} の応答が JSON ではない（${response.status}）`, { text: text.slice(0, 500), cause });
  }
  if (!response.ok) {
    if (typeof body === "object" && body !== null && "written" in body && body.written === true) {
      throw new WrittenButStaleError(`${response.status} ${String((body as { error?: unknown }).error)}`, body);
    }
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

function reviewPath(ontologyId: string): string {
  return `/api/ontologies/${encodeURIComponent(ontologyId)}/review`;
}

export async function getReview(ontologyId: string): Promise<ReviewQueue> {
  return parseReview(await getJson(reviewPath(ontologyId)));
}

/** 1つの問いに答える。応答は答えた後の問いの列。当てられない答え（検証を通らない、など）は 409 の ApiError。 */
export async function postAnswer(ontologyId: string, answer: ReviewAnswer): Promise<ReviewQueue> {
  return parseReview(await postJson(`${reviewPath(ontologyId)}/answers`, answer));
}

/** 一覧で見せた候補を一括で承認する。見せた後に候補が変わっていれば 409 の ApiError（一覧を出し直す）。 */
export async function postBulk(ontologyId: string, bulk: BulkApproval): Promise<ReviewQueue> {
  return parseReview(await postJson(`${reviewPath(ontologyId)}/bulk`, bulk));
}

/** 表ごとの一覧で見せた問いを承認する。見せた後に変わっていれば 409 の ApiError（一覧を出し直す）。 */
export async function postTable(ontologyId: string, batch: TableApproval): Promise<ReviewQueue> {
  return parseReview(await postJson(`${reviewPath(ontologyId)}/table`, batch));
}
