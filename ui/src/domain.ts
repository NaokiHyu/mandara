// ドメインビューワの純粋な部分：たどった道（パンくず）と、近隣の集まりの見出し。
import { type DomainIndex, type DomainRows, type DomainSummary, type InstanceLink, type LinkGroup, type RelatedGroup,
         RelatedKind } from "./om/model";
import { shortName } from "./graph";

/** 集合を絞る親のインスタンス（table の key の行）と、その親への関係。1:n なら relation は子の表から親への関係。
 *  n:m なら relation は relation の表から集合の表への関係、via は relation の表から親への関係（RelatedGroup と同じ）。 */
export type Within = { table: string; key: readonly string[]; relation: string; via?: string };

/** 見ているもの：ドメイン（集合。key が無い）か、そのインスタンス（個）。集合は within で親のインスタンスにつながる部分集合にできる。 */
export type Place = { table: string; key: readonly string[] | null; within?: Within };

export const TRAIL_LIMIT = 12;

export function samePlace(a: Place, b: Place): boolean {
  return a.table === b.table && (a.key ?? []).join("\u0000") === (b.key ?? []).join("\u0000") &&
    (a.key === null) === (b.key === null) && withinKey(a.within) === withinKey(b.within);
}

function withinKey(w: Within | undefined): string {
  return w === undefined ? "" : [w.table, w.relation, w.via ?? "", ...w.key].join("\u0000");
}

/** たどった道に足す。同じ場所に戻ったら、そこから先を切る（行き来で道が伸び続けない）。長さは TRAIL_LIMIT まで。 */
export function extendTrail(trail: readonly Place[], next: Place): Place[] {
  const at = trail.findIndex((p) => samePlace(p, next));
  const kept = at >= 0 ? trail.slice(0, at) : [...trail];
  return [...kept, next].slice(-TRAIL_LIMIT);
}

/** 近隣の集まりの見出し：1:n なら「← 表 参照の列」、n:m なら「⇄ 表」。relation の表はリンクであって物ではないので、
 *  ドメインビューワには出さない。ただし同じ relation の表を別の側から見た集まり（部品表の組立品と部品）が siblings にあれば、
 *  見分けるために「こちらを指す列 から」を添える。 */
export function relatedTitle(group: RelatedGroup, siblings: readonly RelatedGroup[] = []): { mark: string; name: string; detail: string } {
  if (group.kind === RelatedKind.via) {
    const twin = siblings.some((s) => s.kind === RelatedKind.via && s.table === group.table
      && s.via_table === group.via_table && s.via_relation !== group.via_relation);
    const from = (group.via_columns ?? []).join(", ");
    return { mark: "⇄", name: shortName(group.name), detail: twin && from !== "" ? `${from} から` : "" };
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

/** n:m のつながりの値（期間、数量など。relation の行の link_cells）を、つながる先の行（キー）ごとの1行の文字にする。
 *  同じ先に2本以上つながっていれば「 / 」で並べる。値が NULL の列は出さない。つながりは先の行の属性として見せる。 */
export function linkTexts(links: LinkGroup | undefined): Map<string, string> {
  const texts = new Map<string, string[]>();
  for (const r of links?.rows ?? []) {
    const text = (r.link_cells ?? []).filter((c) => c.value !== undefined).map((c) => `${c.column} ${c.value ?? ""}`).join(" · ");
    if (text === "") continue;
    const k = r.key.join("\u0000");
    texts.set(k, [...(texts.get(k) ?? []), text]);
  }
  return new Map([...texts].map(([k, v]) => [k, v.join(" / ")]));
}

export function rowKey(key: readonly string[]): string {
  return key.join("\u0000");
}

/** 数字の帯（KPI）の1つ：近隣の集まりの件数と、合計できる尺度の1つ目（あれば）。値はすべて API の集計（作った数字は無い）。 */
export type Kpi = { key: string; label: string; mark: string; count: number; measure?: { column: string; value?: string; unit?: string } };

export function kpisOf(groups: readonly RelatedGroup[], limit = 4): Kpi[] {
  return groups.slice(0, limit).map((g) => {
    const sum = (g.measures ?? []).find((m) => m.op === "sum");
    return { key: groupKey(g), label: shortName(g.name), mark: relatedTitle(g).mark, count: g.count,
             ...(sum === undefined ? {} : { measure: { column: sum.column, value: sum.value, unit: sum.unit_text } }) };
  });
}

/** 表をドメインビューワで開けるか（API の DomainIndex が決める）：開ける（domain）、開けない理由（hidden）、どちらでもない（none。
 *  一覧に無い表）。両方に入っていれば契約の誤りとしてエラー。 */
export function objectsOf(index: DomainIndex, table: string): { state: "domain" } | { state: "hidden"; reason: string } | { state: "none" } {
  const domain = index.domains.some((d) => d.table === table);
  const hidden = index.hidden.find((h) => h.table === table);
  if (domain && hidden !== undefined) throw new Error(`表 ${table} がドメインと、ドメインでない表の両方にある（DomainIndex の誤り）`);
  if (domain) return { state: "domain" };
  return hidden === undefined ? { state: "none" } : { state: "hidden", reason: hidden.reason };
}

/** 運用室の出来事の並び：時系列のあるものを先に、その中で件数の多い順（同じなら表の ID の順）。並べ替えるだけで、増減しない。 */
export function orderEvents<T extends { rows: Pick<DomainRows, "series" | "count" | "table"> }>(events: readonly T[]): T[] {
  const timed = (e: T) => (e.rows.series !== undefined ? 1 : 0);
  return [...events].sort((a, b) => timed(b) - timed(a) || b.rows.count - a.rows.count || a.rows.table.localeCompare(b.rows.table));
}
