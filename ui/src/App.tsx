import { useEffect, useMemo, useState } from "react";
import { ApiError, getOntology, listOntologies } from "./api";
import type { OntologyDocument, OntologyListing, Table, TableKind } from "./om/model";
import "./App.css";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

// kind は契約（JSON Schema の enum）で検証済み。生成された型では string なので、ここで TableKind として読む
const KIND_MARK: Record<TableKind, string> = { domain: "D", relation: "R", event: "E", derived: "V" };

export function App() {
  const [listing, setListing] = useState<Load<OntologyListing[]>>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [doc, setDoc] = useState<Load<OntologyDocument> | null>(null);

  useEffect(() => {
    listOntologies().then(
      (index) => {
        setListing({ state: "ok", value: index.ontologies });
        const first = index.ontologies[0];
        if (first !== undefined) setSelected((current) => current ?? first.id);
      },
      (error: unknown) => setListing({ state: "error", error }),
    );
  }, []);

  useEffect(() => {
    if (selected === null) return;
    setDoc({ state: "loading" });
    getOntology(selected).then(
      (value) => setDoc({ state: "ok", value }),
      (error: unknown) => setDoc({ state: "error", error }),
    );
  }, [selected]);

  return (
    <div className="shell">
      <header className="bar">
        <span className="brand">◆ MANDARA</span>
        {listing.state === "ok" && (
          <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
            {listing.value.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        )}
        {doc?.state === "ok" && <Summary doc={doc.value} />}
      </header>
      <main className="body">
        {listing.state === "error" && <Failure error={listing.error} />}
        {listing.state === "ok" && listing.value.length === 0 && (
          <p className="dim">見られる OM が無い。mandara run で OM を作るか、mandara serve --gold NAME=DIR で正解 OM を指定する</p>
        )}
        {doc?.state === "error" && <Failure error={doc.error} />}
        {doc?.state === "loading" && <p className="dim">loading…</p>}
        {doc?.state === "ok" && <TableList tables={doc.value.tables} />}
      </main>
    </div>
  );
}

function Summary({ doc }: { doc: OntologyDocument }) {
  const tables = doc.tables;
  const relations = tables.reduce((n, t) => n + (t.relations?.length ?? 0), 0);
  const pending = tables.filter((t) => t.status === "proposed").length;
  return (
    <span className="dim">
      {tables.length} tables · {relations} relations · {pending} pending
    </span>
  );
}

function TableList({ tables }: { tables: Table[] }) {
  const byContext = useMemo(() => {
    const groups = new Map<string, Table[]>();
    for (const t of [...tables].sort((a, b) => a.name.localeCompare(b.name))) {
      groups.set(t.context, [...(groups.get(t.context) ?? []), t]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [tables]);

  return (
    <div className="contexts">
      {byContext.map(([context, group]) => (
        <section key={context} className="context">
          <h2>{context} <span className="dim">{group.length}</span></h2>
          <ul>
            {group.map((t) => (
              <li key={t.id} className={`table status-${t.status}`}>
                <span className={`kind kind-${t.kind ?? "undecided"}`} title={t.kind ?? "kind は未決"}>
                  {t.kind === undefined ? "·" : KIND_MARK[t.kind as TableKind]}
                </span>
                <span className="name">{t.name.split(".").at(-1)}</span>
                <span className="meta dim">
                  {Object.keys(t.columns ?? {}).length}c {t.relations?.length ?? 0}r
                  {t.confidence !== undefined && t.status === "proposed" ? ` ${t.confidence.toFixed(2)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** 失敗はそのまま見せる。API の失敗なら理由と中身、それ以外（通信の失敗、バグ）は種類と内容。 */
function Failure({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return (
      <div className="failure">
        <p>{error.message}</p>
        {error.detail !== undefined && (
          <details><summary>詳細</summary><pre>{JSON.stringify(error.detail, null, 2)}</pre></details>
        )}
      </div>
    );
  }
  const name = error instanceof Error ? error.name : typeof error;
  return <p className="failure">{name}: {error instanceof Error ? error.message : String(error)}</p>;
}
