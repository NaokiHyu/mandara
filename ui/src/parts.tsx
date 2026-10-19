// 小さな部品：ドメインのアイコン、インスタンスの見出し、細線の枠の四隅の印。
import { type InstanceSummary, LabelSource } from "./om/model";
import { asKind, KIND } from "./vocabulary";

/** ドメインのアイコン：形（文字）は種類、色はカテゴリ（エンティティグループ）。 */
export function DomainIcon({ kind, color, big = false }: { kind: string; color: string; big?: boolean }) {
  const k = KIND[asKind(kind)];
  return <span className={`icon${big ? " big" : ""}`} style={{ background: `var(${color})` }}
               title={k.text}>{k.mark}</span>;
}

/** インスタンスの見出し。借りた見出しは印を付け、借りる先の行が無ければ壊れた参照として見せる。 */
export function Label({ summary }: { summary: InstanceSummary }) {
  return (
    <span className="key label">
      {summary.label}
      {summary.label_source === LabelSource.borrowed && <span className="dim small" title="is-a の上位の見出しを借りた"> ↑</span>}
      {summary.label_source === LabelSource.borrowed_missing && (
        <span className="failure small" title="is-a の上位に同じキーの行が無い（壊れた参照）。キーを見出しにした"> 上位の行が無い</span>
      )}
    </span>
  );
}

/** 細線の枠（.blueprint）の四隅の「+」の印。枠の要素の子として置く。 */
export function Corners() {
  return <><i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" /></>;
}
