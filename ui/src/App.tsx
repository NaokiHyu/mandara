import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getOntology, listOntologies } from "./api";
import type { OntologyDocument, OntologyListing, Table } from "./om/model";
import { geoColumns, type GeoColumn } from "./geo";
import { contextColor, GraphView } from "./GraphView";
import { shortName, toGraph } from "./graph";
import { ReviewView } from "./ReviewView";
import { TableDetail } from "./TableDetail";
import { asKind, KIND } from "./vocabulary";
import "./App.css";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

// 地図（MapLibre）は大きいので、地図を開いたときに読む
const MapView = lazy(() => import("./MapView").then((m) => ({ default: m.MapView })));

type View = "graph" | "list" | "map" | "review";
const VIEWS: readonly View[] = ["graph", "list", "map", "review"];

/** URL（?ontology=…&table=…&view=…）で、どの OM のどの表をどう見ているかを表す。開き直しても、共有しても同じものが見える。
 *  view が無ければ graph。知らない view は、黙って置き換えずに伝える（unknownView）。 */
function readUrl(): { ontology: string | null; table: string | null; view: View; unknownView: string | null } {
  const params = new URLSearchParams(location.search);
  const raw = params.get("view");
  const view = raw === null ? "graph" : VIEWS.find((v) => v === raw);
  return {
    ontology: params.get("ontology"),
    table: params.get("table"),
    view: view ?? "graph",
    unknownView: view === undefined ? raw : null,
  };
}

function writeUrl(ontology: string | null, table: string | null, view: View): void {
  const params = new URLSearchParams();
  if (ontology !== null) params.set("ontology", ontology);
  if (table !== null) params.set("table", table);
  if (view !== "graph") params.set("view", view);
  const search = params.toString();
  history.replaceState(null, "", search === "" ? location.pathname : `?${search}`);
}

export function App() {
  const [listing, setListing] = useState<Load<OntologyListing[]>>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(() => readUrl().ontology);
  const [doc, setDoc] = useState<Load<OntologyDocument> | null>(null);
  const [view, setView] = useState<View>(() => readUrl().view);
  const [unknownView] = useState(() => readUrl().unknownView);
  const [table, setTable] = useState<string | null>(() => readUrl().table);
  const select = useCallback((id: string | null) => setTable(id), []);
  const openTable = useCallback((id: string) => { setTable(id); setView("graph"); }, []);

  useEffect(() => writeUrl(selected, table, view), [selected, table, view]);

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

  // レビューで書いた後に読み直す。読み直しの間も今の画面（レビューの状態）は残す
  const refresh = useCallback(() => {
    if (selected === null) return;
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
          {VIEWS.map((v) => (
            <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>
          ))}
        </nav>
      </header>
      <main className="body">
        {unknownView !== null && (
          <p className="failure">表示 {unknownView} は無い（{VIEWS.join(" / ")} のどれか）。graph で開いた</p>
        )}
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
          <Workspace ontologyId={requireSelected(selected)} doc={doc.value} view={view} table={table} onSelect={select}
                     onChanged={refresh} onOpenTable={openTable} />
        )}
      </main>
    </div>
  );
}

/** 文書を読めたなら、どの OM かは決まっている。 */
function requireSelected(selected: string | null): string {
  if (selected === null) throw new Error("OM の文書があるのに、選んでいる OM が無い");
  return selected;
}

function Workspace({ ontologyId, doc, view, table, onSelect, onChanged, onOpenTable }: {
  ontologyId: string;
  doc: OntologyDocument;
  view: View;
  table: string | null;
  onSelect: (id: string | null) => void;
  onChanged: () => void;
  onOpenTable: (id: string) => void;
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
  const geo = useMemo(() => geoColumns(doc), [doc]);
  if (view === "review") {
    return <ReviewView ontologyId={ontologyId} doc={doc} onChanged={onChanged} onOpenTable={onOpenTable} />;
  }
  return (
    <div className="workspace">
      <div className="canvas">
        {table !== null && current === undefined && (
          <p className="failure">表 {table} はこの OM に無い（URL の table を確かめる）</p>
        )}
        {view === "list" && <TableList tables={doc.tables} contexts={contexts} selected={table} onSelect={onSelect} />}
        {view === "map" && <MapPane ontologyId={ontologyId} columns={geo} table={table} />}
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

/** 地図：選んでいる表に geo.point の列があればそれを出す。無ければそう伝え、出せる列から選んでもらう。 */
function MapPane({ ontologyId, columns, table }: { ontologyId: string; columns: GeoColumn[]; table: string | null }) {
  const ofTable = columns.filter((c) => c.tableId === table);
  const first = ofTable[0]; // columns は Workspace で memo 済みなので、表が同じなら同じもの
  const [chosen, setChosen] = useState<GeoColumn | undefined>(first);
  useEffect(() => setChosen(first), [first]);
  if (columns.length === 0) {
    return <p className="dim">この OM には地図に出せる列（意味の型が geo.point の列）が無い</p>;
  }
  const label = (c: GeoColumn) => `${c.tableName}.${c.column}`;
  return (
    <div className="map-pane">
      {table !== null && ofTable.length === 0 && (
        <p className="dim">選んでいる表に geo.point の列は無い。地図に出せる列から選ぶ</p>
      )}
      <select value={chosen === undefined ? "" : label(chosen)}
              onChange={(e) => setChosen(columns.find((c) => label(c) === e.target.value))}>
        {chosen === undefined && <option value="">— 地図に出す列 —</option>}
        {columns.map((c) => <option key={label(c)} value={label(c)}>{label(c)}</option>)}
      </select>
      {chosen === undefined ? null : (
      <Suspense fallback={<p className="dim">loading map…</p>}>
        <MapView ontologyId={ontologyId} target={chosen} />
      </Suspense>
      )}
    </div>
  );
}

function Summary({ doc }: { doc: OntologyDocument }) {
  const tables = doc.tables;
  const relations = tables.reduce((n, t) => n + (t.relations?.length ?? 0), 0);
  const pending = tables.filter((t) => t.status === "proposed").length;
  const approved = tables.filter((t) => t.status === "approved").length;
  return (
    <span className="dim">
      {tables.length} tables · {relations} relations · {approved} approved · {pending} pending
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
                <button className={`table status-${t.status}${t.derived ? " derived" : ""}${t.id === selected ? " selected" : ""}`}
                        aria-pressed={t.id === selected} onClick={() => onSelect(t.id)}>
                  <span className={`kind kind-${t.kind ?? "undecided"}`}
                        title={(t.kind === undefined ? "kind は未決" : KIND[asKind(t.kind)].text) + (t.derived ? "（計算で導出された表）" : "")}>
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
