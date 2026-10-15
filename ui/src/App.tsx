import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getOntology, listOntologies } from "./api";
import type { OntologyDocument, OntologyListing, Table } from "./om/model";
import { contextColor, GraphView } from "./GraphView";
import { shortName, toGraph } from "./graph";
import { TableDetail } from "./TableDetail";
import { asKind, KIND } from "./vocabulary";
import "./App.css";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

/** URL（?ontology=…&table=…）で、どの OM のどの表を見ているかを表す。開き直しても、共有しても同じものが見える。 */
function readUrl(): { ontology: string | null; table: string | null } {
  const params = new URLSearchParams(location.search);
  return { ontology: params.get("ontology"), table: params.get("table") };
}

function writeUrl(ontology: string | null, table: string | null): void {
  const params = new URLSearchParams();
  if (ontology !== null) params.set("ontology", ontology);
  if (table !== null) params.set("table", table);
  const search = params.toString();
  history.replaceState(null, "", search === "" ? location.pathname : `?${search}`);
}

export function App() {
  const [listing, setListing] = useState<Load<OntologyListing[]>>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(() => readUrl().ontology);
  const [doc, setDoc] = useState<Load<OntologyDocument> | null>(null);
  const [view, setView] = useState<"graph" | "list">("graph");
  const [table, setTable] = useState<string | null>(() => readUrl().table);
  const select = useCallback((id: string | null) => setTable(id), []);

  useEffect(() => writeUrl(selected, table), [selected, table]);

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
          <select value={selected ?? ""} onChange={(e) => { setSelected(e.target.value); setTable(null); }}>
            {listing.value.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        )}
        {doc?.state === "ok" && <Summary doc={doc.value} />}
        <nav className="views">
          {(["graph", "list"] as const).map((v) => (
            <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>
          ))}
        </nav>
      </header>
      <main className="body">
        {listing.state === "error" && <Failure error={listing.error} />}
        {listing.state === "ok" && selected !== null && listing.value.length > 0 &&
          !listing.value.some((o) => o.id === selected) && (
          <p className="failure">OM {selected} は見られる OM に無い（URL の ontology を確かめる）</p>
        )}
        {listing.state === "ok" && listing.value.length === 0 && (
          <p className="dim">見られる OM が無い。mandara run で OM を作るか、mandara serve --gold NAME=DIR で正解 OM を指定する</p>
        )}
        {doc?.state === "error" && <Failure error={doc.error} />}
        {doc?.state === "loading" && <p className="dim">loading…</p>}
        {doc?.state === "ok" && (
          <Workspace doc={doc.value} view={view} table={table} onSelect={select} />
        )}
      </main>
    </div>
  );
}

function Workspace({ doc, view, table, onSelect }: {
  doc: OntologyDocument;
  view: "graph" | "list";
  table: string | null;
  onSelect: (id: string | null) => void;
}) {
  const graph = useMemo((): Load<ReturnType<typeof toGraph>> => {
    try {
      return { state: "ok", value: toGraph(doc) };
    } catch (error) {
      return { state: "error", error };
    }
  }, [doc]);
  const current = doc.tables.find((t) => t.id === table);
  const contexts = useMemo(() => [...new Set(doc.tables.map((t) => t.context))].sort(), [doc]);
  return (
    <div className="workspace">
      <div className="canvas">
        {table !== null && current === undefined && (
          <p className="failure">表 {table} はこの OM に無い（URL の table を確かめる）</p>
        )}
        {view === "list" && <TableList tables={doc.tables} contexts={contexts} selected={table} onSelect={onSelect} />}
        {view === "graph" && graph.state === "error" && <Failure error={graph.error} />}
        {view === "graph" && graph.state === "ok" && (
          <GraphView nodes={graph.value.nodes} edges={graph.value.edges} contexts={contexts} selected={table}
                     onSelect={onSelect} />
        )}
      </div>
      {current !== undefined && <TableDetail doc={doc} table={current} onSelect={onSelect} />}
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

function TableList({ tables, contexts, selected, onSelect }: {
  tables: Table[];
  contexts: string[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const byContext = useMemo(() => contexts.map((context) => [
    context, tables.filter((t) => t.context === context).sort((a, b) => a.name.localeCompare(b.name)),
  ] as const), [tables, contexts]);

  return (
    <div className="contexts">
      {byContext.map(([context, group], i) => (
        <section key={context} className="context" style={{ borderTopColor: `var(${contextColor(i)})` }}>
          <h2>{context} <span className="dim">{group.length}</span></h2>
          <ul>
            {group.map((t) => (
              <li key={t.id}>
                <button className={`table status-${t.status}${t.id === selected ? " selected" : ""}`}
                        aria-pressed={t.id === selected} onClick={() => onSelect(t.id)}>
                  <span className={`kind kind-${t.kind ?? "undecided"}`}
                        title={t.kind === undefined ? "kind は未決" : KIND[asKind(t.kind)].text}>
                    {t.kind === undefined ? "·" : KIND[asKind(t.kind)].mark}
                  </span>
                  <span className="name">{shortName(t.name)}</span>
                  <span className="meta dim">
                    {Object.keys(t.columns ?? {}).length}c {t.relations?.length ?? 0}r
                    {t.confidence !== undefined && t.status === "proposed" ? ` ${t.confidence.toFixed(2)}` : ""}
                  </span>
                </button>
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
