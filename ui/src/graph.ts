// OM の文書 → グラフの要素（表がノード、関係・辺がエッジ）。描画（Cytoscape.js）からは独立した純粋な関数。
// 関係の参照先の解決と、結合の表し方はここに1つだけ置く（詳細パネルも使う）。
import type { OntologyDocument, Relation, RelationType, Table } from "./om/model";
import { asRelationType } from "./vocabulary";

export interface GraphNode {
  id: string;
  label: string;
  name: string;
  entityGroup: string;
  /** エンティティグループの番号（名前の順）。色の割り当てに使う。 */
  groupIndex: number;
  kind: string | undefined;
  /** 計算で導出された表（ビューなど）。kind とは別の軸 */
  derived: boolean;
  status: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: RelationType;
  /** 表の間の関係か、中間表の辺（行が実現する、もの同士の関係）か。 */
  via: "relation" | "edge";
  /** 結合（列の対応、関数）。辺なら中間表の名前。 */
  label: string;
  status: string;
}

export class GraphError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GraphError";
  }
}

export function shortName(name: string): string {
  return name.split(".").at(-1) ?? name;
}

/** 関係の参照先の表。self は自分自身。見つからなければ GraphError（検証を通った OM では起きない）。 */
export function resolveTarget(table: Table, relation: Relation, byId: ReadonlyMap<string, Table>): Table {
  if (relation.target === "self") return table;
  const target = byId.get(relation.target);
  if (target === undefined) {
    throw new GraphError(`${table.name} の関係 ${relation.id} の参照先 ${relation.target} が OM に無い`);
  }
  return target;
}

/** 関係が何で結ばれているか（列の対応、結ぶ関数、導出の関数と列）。 */
export function joins(relation: Relation): string {
  if (relation.via && relation.via.length > 0) return relation.via.map((p) => `${p.from} → ${p.to}`).join(", ");
  if (relation.via_function) return relation.via_function;
  if (relation.function) return `${relation.function}(${(relation.columns ?? []).join(", ")})`;
  throw new GraphError(`関係 ${relation.id} に結合（via / via_function / function）が無い`);
}

export function toGraph(doc: OntologyDocument): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const byId = new Map(doc.tables.map((t) => [t.id, t]));
  const entityGroups = [...new Set(doc.tables.map((t) => t.entity_group))].sort();
  const nodes = doc.tables.map((t) => ({
    id: t.id, label: shortName(t.name), name: t.name, entityGroup: t.entity_group, groupIndex: entityGroups.indexOf(t.entity_group),
    kind: t.kind, derived: t.derived === true, status: t.status,
  }));
  const edges: GraphEdge[] = [];
  for (const table of doc.tables) {
    for (const relation of table.relations ?? []) {
      edges.push({
        id: `${table.id}|relation|${relation.id}`,
        source: table.id,
        target: resolveTarget(table, relation, byId).id,
        type: asRelationType(relation.type),
        via: "relation",
        label: joins(relation),
        status: relation.status,
      });
    }
    if (table.edge) {
      const relations = new Map((table.relations ?? []).map((r) => [r.id, r]));
      const from = relations.get(table.edge.from);
      const to = relations.get(table.edge.to);
      if (from === undefined || to === undefined) {
        throw new GraphError(`${table.name} の辺が、この表に無い関係を指している`);
      }
      edges.push({
        id: `${table.id}|edge`,
        source: resolveTarget(table, from, byId).id,
        target: resolveTarget(table, to, byId).id,
        type: asRelationType(table.edge.type),
        via: "edge",
        label: shortName(table.name),
        status: table.edge.status,
      });
    }
  }
  return { nodes, edges };
}

/** エンティティグループの色のトークンの数。これより多いエンティティグループは色が巡回する（凡例に出すので、見えない衝突にはしない）。 */
export const GROUP_COLORS = 6;

/** エンティティグループ（名前の順の位置）の色のトークン。グラフ、一覧、ドメインビューワで同じ割り当て。 */
export function groupColor(index: number): string {
  return `--group-${index % GROUP_COLORS}`;
}
