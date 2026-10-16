// 表の詳細：種類、粒度、レビューの状態と確信度、同一性、列（意味型、単位、enum の値）、関係、辺。
// 参照先の解決と結合の表し方は graph.ts のものを使う（二重に持たない）。解決できなければ、隠さずにエラーを出す。
import type { OntologyDocument, Relation, SemanticType, Table } from "./om/model";
import { GraphError, joins, resolveTarget, shortName } from "./graph";
import { asKind, asRelationType, KIND, RELATION } from "./vocabulary";

export function TableDetail({ doc, table, onSelect }: {
  doc: OntologyDocument;
  table: Table;
  onSelect: (tableId: string) => void;
}) {
  const byId = new Map(doc.tables.map((t) => [t.id, t]));
  const semanticTypes = doc.vocabulary.semantic_types ?? {};

  return (
    <aside className="detail" aria-label={`表 ${table.name} の詳細`}>
      <header>
        <h1>{shortName(table.name)}</h1>
        <div className="dim">{table.name} · {table.source}</div>
      </header>

      <dl className="facts">
        <dt>kind</dt><dd>{table.kind === undefined ? <span className="dim">未決</span> : KIND[asKind(table.kind)].text}</dd>
        {table.derived && (<><dt>derived</dt><dd>計算で導出された表（ビューなど）</dd></>)}
        <dt>grain</dt><dd>{table.grain ?? <span className="dim">—</span>}</dd>
        <dt>review</dt><dd><Status status={table.status} confidence={table.confidence} /></dd>
        {table.identity && (
          <>
            <dt>key</dt><dd>{table.identity.primary.join(", ")}</dd>
            {(table.identity.alternates ?? []).map((k, i) => (
              <span key={i} style={{ display: "contents" }}><dt className="dim">alt</dt><dd>{k.columns.join(", ")}</dd></span>
            ))}
          </>
        )}
        {table.note && (<><dt>note</dt><dd className="dim">{table.note}</dd></>)}
      </dl>

      <h2>columns <span className="dim">{Object.keys(table.columns ?? {}).length}</span></h2>
      <table className="grid">
        <thead>
          <tr><th>name</th><th>semantic type · values</th><th>unit</th></tr>
        </thead>
        <tbody>
          {Object.values(table.columns ?? {}).map((c) => (
            <tr key={c.name} className={`status-${c.status}`}>
              <td className="name">{c.name}</td>
              <td>
                {c.semantic_type ?? <span className="dim">—</span>}
                {c.semantic_type !== undefined && <EnumValues semanticType={semanticTypes[c.semantic_type]} />}
              </td>
              <td className="dim">{c.unit?.fixed ?? (c.unit?.column !== undefined ? `per ${c.unit.column}` : null)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>relations <span className="dim">{table.relations?.length ?? 0}</span></h2>
      <ul className="relations">
        {(table.relations ?? []).map((r) => (
          <RelationItem key={r.id} table={table} relation={r} byId={byId} onSelect={onSelect} />
        ))}
      </ul>

      {table.edge && (
        <>
          <h2>edge</h2>
          <p>
            行が <RelationTypeLabel type={table.edge.type} /> の辺：{table.edge.from} → {table.edge.to}
          </p>
        </>
      )}
    </aside>
  );
}

function RelationItem({ table, relation, byId, onSelect }: {
  table: Table;
  relation: Relation;
  byId: ReadonlyMap<string, Table>;
  onSelect: (tableId: string) => void;
}) {
  let target: Table;
  let joined: string;
  try {
    target = resolveTarget(table, relation, byId);
    joined = joins(relation);
  } catch (error) {
    if (!(error instanceof GraphError)) throw error;
    return <li className="failure">{error.message}</li>;
  }
  return (
    <li className={`status-${relation.status}`}>
      <RelationTypeLabel type={relation.type} />{relation.role ? ` (${relation.role})` : ""}{" "}
      <button className="link" onClick={() => onSelect(target.id)}>{shortName(target.name)}</button>
      <div className="dim small">
        {joined} · <Status status={relation.status} confidence={relation.confidence} />
      </div>
    </li>
  );
}

function RelationTypeLabel({ type }: { type: string }) {
  const style = RELATION[asRelationType(type)];
  return <span title={style.text} style={{ color: `var(${style.color})` }}>{type}</span>;
}

function Status({ status, confidence }: { status: string; confidence?: number | undefined }) {
  return (
    <span className={`review review-${status}`}>
      {status}{status === "proposed" && confidence !== undefined ? ` ${confidence.toFixed(2)}` : ""}
    </span>
  );
}

function EnumValues({ semanticType }: { semanticType: SemanticType | undefined }) {
  if (semanticType?.values === undefined || semanticType.values.length === 0) return null;
  return <div className="dim small">{semanticType.values.map((v) => `${v.code}=${v.label}`).join(" · ")}</div>;
}
