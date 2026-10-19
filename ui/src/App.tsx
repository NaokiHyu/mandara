import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getDomains, getOntology, listOntologies } from "./api";
import type { DomainIndex, OntologyDocument, OntologyListing, Table } from "./om/model";
import { geoColumns, type GeoColumn } from "./geo";
import { GraphLegend, GraphView } from "./GraphView";
import { objectsOf, type Place, type Within } from "./domain";
import { groupColor } from "./graph";
import { shortName, toGraph } from "./graph";
import { DomainView } from "./DomainView";
import { ReviewView } from "./ReviewView";
import { OpsRoom } from "./OpsRoom";
import { TableDetail } from "./TableDetail";
import { asKind, KIND } from "./vocabulary";
import "./App.css";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

// 地図（MapLibre）は大きいので、地図を開いたときに読む
const MapView = lazy(() => import("./MapView").then((m) => ({ default: m.MapView })));

type View = "ops" | "graph" | "list" | "map" | "review" | "domain";
const VIEWS: readonly View[] = ["ops", "graph", "list", "map", "review", "domain"];
/** ナビの見出し（URL の view の値はそのまま）。 */
const VIEW_LABELS: Record<View, string> = {
  ops: "運用室", graph: "グラフ", list: "一覧", map: "地図", review: "レビュー", domain: "ドメイン",
};

/** URL（?ontology=…&table=…&view=…&key=…）で、どの OM のどの表をどう見ているかを表す。開き直しても、共有しても同じものが見える。
 *  view が無ければ graph。知らない view は、黙って置き換えずに伝える（unknownView）。key はドメインビューワのインスタンス（主キーの値）。 */
function readUrl(): { ontology: string | null; table: string | null; view: View; unknownView: string | null;
                      key: string[] | null; within: Within | undefined; partialWithin: boolean } {
  const params = new URLSearchParams(location.search);
  const raw = params.get("view");
  const view = raw === null ? "graph" : VIEWS.find((v) => v === raw);
  const withinParts = ["within_table", "within_key", "within_relation"].map((k) => params.has(k));
  return {
    ontology: params.get("ontology"),
    table: params.get("table"),
    view: view ?? "graph",
    unknownView: view === undefined ? raw : null,
    key: params.has("key") ? params.getAll("key") : null,
    within: withinParts.every(Boolean)
      ? { table: params.get("within_table") ?? "", key: params.getAll("within_key"),
          relation: params.get("within_relation") ?? "", ...(params.has("within_via") ? { via: params.get("within_via") ?? "" } : {}) }
      : undefined,
    partialWithin: (withinParts.some(Boolean) || params.has("within_via")) && !withinParts.every(Boolean),
  };
}

function writeUrl(ontology: string | null, table: string | null, view: View, key: readonly string[] | null,
                  within: Within | undefined): void {
  const params = new URLSearchParams();
  if (ontology !== null) params.set("ontology", ontology);
  if (table !== null) params.set("table", table);
  if (view !== "graph") params.set("view", view);
  if (view === "domain" && key !== null) for (const k of key) params.append("key", k);
  if (view === "domain" && key === null && within !== undefined) {
    params.set("within_table", within.table);
    for (const k of within.key) params.append("within_key", k);
    params.set("within_relation", within.relation);
    if (within.via !== undefined) params.set("within_via", within.via);
  }
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
  const [instanceKey, setInstanceKey] = useState<string[] | null>(() => readUrl().key);
  const [within, setWithin] = useState<Within | undefined>(() => readUrl().within);
  const [partialWithin] = useState(() => readUrl().partialWithin);
  const select = useCallback((id: string | null) => setTable(id), []);
  const openTable = useCallback((id: string) => { setTable(id); setView("graph"); }, []);

  useEffect(() => writeUrl(selected, table, view, instanceKey, within), [selected, table, view, instanceKey, within]);
  const place = useCallback((p: Place | null) => {
    setTable(p?.table ?? null);
    setInstanceKey(p?.key === null || p === null ? null : [...p.key]);
    setWithin(p?.within);
  }, []);

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
        <span className="brand"><i className="brand-mark" />MANDARA</span>
        {listing.state === "ok" && (
          <select value={selected ?? ""} onChange={(e) => { setSelected(e.target.value); setTable(null); setInstanceKey(null); }}>
            {listing.value.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        )}
        {doc?.state === "ok" && <Summary doc={doc.value} />}
        <nav className="views">
          {VIEWS.map((v) => (
            <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>
              {VIEW_LABELS[v]}
            </button>
          ))}
        </nav>
        <Clock />
      </header>
      <main className="body">
        {partialWithin && (
          <p className="failure">URL の within_table・within_key・within_relation がそろっていない（within_via はそれらと一緒に）ので、絞らずに開いた</p>
        )}
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
          view === "domain" ? (
            <DomainView ontologyId={requireSelected(selected)}
                        place={table === null ? null : { table, key: instanceKey, ...(within ? { within } : {}) }}
                        onPlace={place} />
          ) : (
            <Workspace ontologyId={requireSelected(selected)} doc={doc.value} view={view} table={table} onSelect={select}
                       onChanged={refresh} onOpenTable={openTable}
                       onOpenDomain={(p) => { place(p); setView("domain"); }} onReview={() => setView("review")}
                       reviewable={listing.state === "ok" ? listing.value.find((o) => o.id === selected)?.reviewable ?? null : null} />
          )
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

function Workspace({ ontologyId, doc, view, table, onSelect, onChanged, onOpenTable, onOpenDomain, onReview, reviewable }: {
  ontologyId: string;
  doc: OntologyDocument;
  view: View;
  table: string | null;
  onSelect: (id: string | null) => void;
  onChanged: () => void;
  onOpenTable: (id: string) => void;
  onOpenDomain: (place: Place) => void;
  onReview: () => void;
  /** レビューの列を読める OM か（API の一覧の値）。一覧に無ければ null */
  reviewable: boolean | null;
}) {
  const graph = useMemo((): Load<ReturnType<typeof toGraph>> => {
    try {
      return { state: "ok", value: toGraph(doc) };
    } catch (error) {
      return { state: "error", error };
    }
  }, [doc]);
  const current = doc.tables.find((t) => t.id === table);
  const entityGroups = useMemo(() => [...new Set(doc.tables.map((t) => t.entity_group))].sort(), [doc]);
  const geo = useMemo(() => geoColumns(doc), [doc]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of doc.tables) m.set(t.entity_group, (m.get(t.entity_group) ?? 0) + 1);
    return m;
  }, [doc]);
  // どの表をドメインビューワで開けるか（API が決める。承認の後で変わるので OM を読み直すたびに）。表の詳細を出す画面だけで読む
  const detailShown = view === "graph" || view === "list" || view === "map";
  const [domains, setDomains] = useState<Load<DomainIndex>>({ state: "loading" });
  useEffect(() => {
    if (!detailShown) return;
    let live = true;
    setDomains({ state: "loading" });
    getDomains(ontologyId).then((value) => { if (live) setDomains({ state: "ok", value }); },
                                (error: unknown) => { if (live) setDomains({ state: "error", error }); });
    return () => { live = false; };
  }, [ontologyId, doc, detailShown]);
  const objects = (id: string) => {
    if (domains.state !== "ok") return undefined;
    const found = objectsOf(domains.value, id);
    return found.state === "domain" ? { open: () => onOpenDomain({ table: id, key: null }) }
      : found.state === "hidden" ? { reason: found.reason } : undefined;
  };
  if (view === "ops") {
    return <OpsRoom ontologyId={ontologyId} doc={doc} geo={geo} onOpenDomain={onOpenDomain} onReview={onReview}
                    reviewable={reviewable} />;
  }
  if (view === "review") {
    return <ReviewView ontologyId={ontologyId} doc={doc} onChanged={onChanged} onOpenTable={onOpenTable} />;
  }
  return (
    <div className="workspace">
      {view === "graph" && <GraphLegend entityGroups={entityGroups} counts={counts} />}
      <div className={`canvas${view === "graph" ? " graph-canvas" : ""}`}>
        {domains.state === "error" && <Failure error={domains.error} />}
        {table !== null && current === undefined && (
          <p className="failure">表 {table} はこの OM に無い（URL の table を確かめる）</p>
        )}
        {view === "list" && <TableList tables={doc.tables} entityGroups={entityGroups} selected={table} onSelect={onSelect} />}
        {view === "map" && <MapPane ontologyId={ontologyId} columns={geo} table={table} onOpenDomain={onOpenDomain} />}
        {view === "graph" && graph.state === "error" && <Failure error={graph.error} />}
        {view === "graph" && graph.state === "ok" && (
          <GraphView nodes={graph.value.nodes} edges={graph.value.edges} entityGroups={entityGroups} selected={table}
                     onSelect={onSelect} />
        )}
      </div>
      {current !== undefined && <TableDetail doc={doc} table={current} onSelect={onSelect} objects={objects(current.id)} />}
    </div>
  );
}

/** 地図：選んでいる表に geo.point の列があればそれを出す。無ければそう伝え、出せる列から選んでもらう。 */
function MapPane({ ontologyId, columns, table, onOpenDomain }: {
  ontologyId: string; columns: GeoColumn[]; table: string | null; onOpenDomain: (place: Place) => void;
}) {
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
        <MapView ontologyId={ontologyId} target={chosen} onOpenDomain={onOpenDomain} />
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
  const entityGroups = new Set(tables.map((t) => t.entity_group)).size;
  return (
    <span className="summary">
      表 {tables.length} · エンティティグループ {entityGroups} · 関係 {relations} · 承認済み {approved}
      {pending > 0 && <span className="warn"> · 未決 {pending}</span>}
    </span>
  );
}

/** UTC の時計（秒まで）。 */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span className="clock" title="UTC">{now.toISOString().slice(11, 19)} <i>UTC</i></span>;
}

function TableList({ tables, entityGroups, selected, onSelect }: {
  tables: Table[];
  entityGroups: string[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const byGroup = useMemo(() => entityGroups.map((entityGroup) => [
    entityGroup, tables.filter((t) => t.entity_group === entityGroup).sort((a, b) => a.name.localeCompare(b.name)),
  ] as const), [tables, entityGroups]);

  return (
    <div className="entity-groups">
      {byGroup.map(([entityGroup, group], i) => (
        <section key={entityGroup} className="entity-group" style={{ borderTopColor: `var(${groupColor(i)})` }}>
          <h2>{entityGroup} <span className="dim">{group.length}</span></h2>
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
