// ドメインビューワの純粋な部分：たどった道（パンくず）と、近隣の集まりの見出し。
import { type DomainSummary, type InstanceLink, type RelatedGroup, RelatedKind } from "./om/model";
import { shortName } from "./graph";

/** 見ているもの：ドメイン（集合。key が無い）か、そのインスタンス（個）。 */
export type Place = { table: string; key: readonly string[] | null };

export const TRAIL_LIMIT = 12;

export function samePlace(a: Place, b: Place): boolean {
  return a.table === b.table && (a.key ?? []).join("\u0000") === (b.key ?? []).join("\u0000") &&
    (a.key === null) === (b.key === null);
}

/** たどった道に足す。同じ場所に戻ったら、そこから先を切る（行き来で道が伸び続けない）。長さは TRAIL_LIMIT まで。 */
export function extendTrail(trail: readonly Place[], next: Place): Place[] {
  const at = trail.findIndex((p) => samePlace(p, next));
  const kept = at >= 0 ? trail.slice(0, at) : [...trail];
  return [...kept, next].slice(-TRAIL_LIMIT);
}

/** 近隣の集まりの見出し：1:n なら「← 表（参照の列）」、n:m なら「⇄ 表 via 中間の表」。 */
export function relatedTitle(group: RelatedGroup): { mark: string; name: string; detail: string } {
  if (group.kind === RelatedKind.via) {
    const from = (group.via_columns ?? []).join(", ");
    return { mark: "⇄", name: shortName(group.name),
             detail: `via ${shortName(group.via_name ?? "")}${from === "" ? "" : `（${from} から）`}` };
  }
  return { mark: "←", name: shortName(group.name), detail: group.columns.join(", ") };
}

/** 近隣の集まりの鍵（同じ relation の表を別の側から見た集まりも区別する）。 */
export function groupKey(group: RelatedGroup): string {
  return `${group.kind}/${group.via_table ?? ""}/${group.via_relation ?? ""}/${group.relation}`;
}

/** 参照先へのリンクの見出し：役割があれば役割、無ければ参照の列。 */
export function linkTitle(link: InstanceLink): string {
  return link.role ?? link.columns.join(", ");
}

/** 識別の列（主キー、見出し、自然キー）か。高コントラストで見せる。 */
export function emphasized(domain: Pick<DomainSummary, "key_columns" | "label_columns" | "natural_keys">,
                           column: string): boolean {
  return domain.key_columns.includes(column) || domain.label_columns.includes(column)
    || domain.natural_keys.includes(column);
}
