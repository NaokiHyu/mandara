// ドメインビューワ（承認済みの OM から。仕様 7.6）：ドメイン（集合。表のイメージ）とインスタンス（個。行のイメージ）。
// relation の表は出さずにリンクとしてたどる。値はソースをその場で読む（ゼロコピー）。読めなかったら理由をそのまま見せる。
import { useEffect, useMemo, useState } from "react";
import { ApiError, getDomainRows, getDomains, getInstance } from "./api";
import { extendTrail, linkTitle, type Place, relatedTitle, samePlace } from "./domain";
import { shortName } from "./graph";
import { type DomainIndex, type DomainRows, type DomainSummary, type Instance, type InstanceSummary,
         LinkState } from "./om/model";
import { asKind, KIND } from "./vocabulary";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

const SEARCH_DELAY_MS = 250;

export function DomainView({ ontologyId, place, onPlace }: {
  ontologyId: string;
  place: Place | null;
  onPlace: (place: Place | null) => void;
}) {
  const [index, setIndex] = useState<Load<DomainIndex>>({ state: "loading" });
  const [filter, setFilter] = useState("");
  const [trail, setTrail] = useState<Place[]>(place === null ? [] : [place]);

  useEffect(() => {
    setIndex({ state: "loading" });
    getDomains(ontologyId).then((value) => setIndex({ state: "ok", value }),
                                (error: unknown) => setIndex({ state: "error", error }));
  }, [ontologyId]);

  const go = (next: Place) => { setTrail((t) => extendTrail(t, next)); onPlace(next); };

  if (index.state === "loading") return <p className="dim pad">loading domains…</p>;
  if (index.state === "error") return <ErrorText error={index.error} />;
  const domains = index.value.domains;
  const byTable = new Map(domains.map((d) => [d.table, d]));
  const current = place === null ? undefined : byTable.get(place.table);

  return (
    <div className="domains">
      <aside className="domain-list" aria-label="ドメイン">
        <header>
          <span className="label">DOMAINS</span>
          <span className="count">{domains.length}</span>
        </header>
        <input className="domain-filter" placeholder="ドメインを絞る…" value={filter}
               onChange={(e) => setFilter(e.target.value)} />
        <DomainList domains={domains} filter={filter} selected={place?.table ?? null}
                    onOpen={(table) => go({ table, key: null })} />
        {index.value.hidden.length > 0 && (
          <details className="hidden-tables">
            <summary className="dim">ドメインにしていない表 {index.value.hidden.length}</summary>
            <ul>{index.value.hidden.map((h) => <li key={h.table}>{shortName(h.name)} <span className="dim">{h.reason}</span></li>)}</ul>
          </details>
        )}
      </aside>
      <section className="domain-main">
        {!index.value.readable && (
          <p className="banner warn">ソースに接続していないドメインがある（mandara serve --connect SOURCE CONNINFO）</p>
        )}
        <Trail trail={trail} byTable={byTable} current={place} onGo={go} />
        {place === null || current === undefined ? (
          <div className="done">
            <div className="done-mark">◆</div>
            <p>{place === null ? "ドメインを選ぶ" : `${place.table} はドメインではない`}</p>
            <p className="dim">承認済みの domain / event の表がドメイン。relation の表はリンクとしてたどる</p>
          </div>
        ) : place.key === null ? (
          <Collection key={current.table} ontologyId={ontologyId} domain={current} onOpen={go} />
        ) : (
          <InstancePane key={`${current.table}/${place.key.join("/")}`} ontologyId={ontologyId} domain={current}
                        instanceKey={place.key} known={byTable} onOpen={go} />
        )}
      </section>
    </div>
  );
}

function DomainList({ domains, filter, selected, onOpen }: {
  domains: DomainSummary[];
  filter: string;
  selected: string | null;
  onOpen: (table: string) => void;
}) {
  const groups = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const shown = domains.filter((d) => f === "" || d.name.toLowerCase().includes(f));
    const contexts = [...new Set(shown.map((d) => d.context))].sort();
    return contexts.map((c) => [c, shown.filter((d) => d.context === c)] as const);
  }, [domains, filter]);
  return (
    <div className="domain-groups">
      {groups.map(([context, group]) => (
        <section key={context}>
          <h2>{context}</h2>
          <ul>
            {group.map((d) => (
              <li key={d.table}>
                <button className={`table${d.table === selected ? " selected" : ""}`} aria-pressed={d.table === selected}
                        onClick={() => onOpen(d.table)}>
                  <span className={`kind kind-${d.kind}`} title={KIND[asKind(d.kind)].text}>{KIND[asKind(d.kind)].mark}</span>
                  <span className="name">{shortName(d.name)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Trail({ trail, byTable, current, onGo }: {
  trail: Place[];
  byTable: Map<string, DomainSummary>;
  current: Place | null;
  onGo: (p: Place) => void;
}) {
  if (trail.length === 0) return null;
  return (
    <nav className="trail" aria-label="たどった道">
      {trail.map((p, i) => (
        <span key={`${p.table}/${(p.key ?? []).join("/")}/${i}`}>
          {i > 0 && <span className="dim sep">›</span>}
          <button className={`link${current !== null && samePlace(p, current) ? " on" : ""}`} onClick={() => onGo(p)}>
            {shortName(byTable.get(p.table)?.name ?? p.table)}{p.key === null ? "" : ` #${p.key.join("·")}`}
          </button>
        </span>
      ))}
    </nav>
  );
}

/** 集合（表のイメージ）：検索窓と結果。結果の行を開くとインスタンス。 */
function Collection({ ontologyId, domain, onOpen }: {
  ontologyId: string;
  domain: DomainSummary;
  onOpen: (p: Place) => void;
}) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Load<DomainRows>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      setRows({ state: "loading" });
      getDomainRows(ontologyId, domain.table, query).then(
        (value) => { if (live) setRows({ state: "ok", value }); },
        (error: unknown) => { if (live) setRows({ state: "error", error }); });
    }, query === "" ? 0 : SEARCH_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [ontologyId, domain.table, query]);

  return (
    <article className="collection" aria-label="ドメイン">
      <header className="domain-head">
        <span className={`kind kind-${domain.kind}`}>{KIND[asKind(domain.kind)].mark}</span>
        <h1>{shortName(domain.name)}</h1>
        <span className="dim">{domain.name}</span>
      </header>
      {domain.grain !== undefined && <p className="grain dim">{domain.grain}</p>}
      <input className="search" autoFocus placeholder={domain.searchable.length > 0
        ? `検索：${domain.searchable.slice(0, 4).join(", ")}${domain.searchable.length > 4 ? " …" : ""} とキー`
        : "検索：キー"} value={query} onChange={(e) => setQuery(e.target.value)} />
      {rows.state === "loading" && <p className="dim">searching…</p>}
      {rows.state === "error" && <ErrorText error={rows.error} />}
      {rows.state === "ok" && (
        <>
          <p className="dim small">{rows.value.rows.length}{rows.value.truncated ? "+" : ""} 件
            {rows.value.truncated ? `（先頭 ${rows.value.limit} 件。絞り込むと続きが見つかる）` : ""}</p>
          <table className="rows">
            <thead>
              <tr><th>key</th><th title={labelTitle(domain)}>label</th>
                {cellColumns(rows.value, domain).map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.value.rows.map((r) => (
                <tr key={r.key.join("/")} onClick={() => onOpen({ table: r.table, key: r.key })} tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen({ table: r.table, key: r.key }); }}>
                  <td className="dim">{r.key.join("·")}</td>
                  <td className="label">{r.label}</td>
                  {r.cells.map((c) => <td key={c.column}>{c.value ?? <span className="null">—</span>}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </article>
  );
}

/** 一覧の列（契約の DomainRows.columns から見出しの列を除いたもの。行の cells はこの順）。 */
function cellColumns(rows: DomainRows, domain: DomainSummary): string[] {
  return rows.columns.filter((c) => !domain.label_columns.includes(c));
}

function labelTitle(domain: DomainSummary): string {
  return domain.label_columns.length === 0 ? "見出しの列が無いので、キーを見出しにしている"
    : `見出しは ${domain.label_columns.join(" + ")}（列の名前からの推定）`;
}

/** 個（行のイメージ）：値、参照先へのリンク、近隣の集まり。 */
function InstancePane({ ontologyId, domain, instanceKey, known, onOpen }: {
  ontologyId: string;
  domain: DomainSummary;
  instanceKey: readonly string[];
  known: Map<string, DomainSummary>;
  onOpen: (p: Place) => void;
}) {
  const [shown, setShown] = useState<Load<Instance>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    getInstance(ontologyId, domain.table, instanceKey).then(
      (value) => { if (live) setShown({ state: "ok", value }); },
      (error: unknown) => { if (live) setShown({ state: "error", error }); });
    return () => { live = false; };
  }, [ontologyId, domain.table, instanceKey]);

  if (shown.state === "loading") return <p className="dim">loading instance…</p>;
  if (shown.state === "error") return <ErrorText error={shown.error} />;
  const it = shown.value;
  const open = (s: InstanceSummary) => onOpen({ table: s.table, key: s.key });
  return (
    <article className="instance" aria-label="インスタンス">
      <header className="domain-head">
        <span className={`kind kind-${domain.kind}`}>{KIND[asKind(domain.kind)].mark}</span>
        <h1>{it.label}</h1>
        <button className="link" onClick={() => onOpen({ table: domain.table, key: null })}>{shortName(it.name)}</button>
        <span className="dim">#{it.key.join("·")}</span>
      </header>
      <div className="instance-body">
        <section className="fields" aria-label="値">
          <table className="grid">
            <tbody>
              {it.fields.map((f) => (
                <tr key={f.column}>
                  <th>{f.column}</th>
                  <td>{f.value ?? <span className="null">—</span>}</td>
                  <td className="dim small">{f.semantic_type ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="neighbors" aria-label="近隣">
          {it.links.length > 0 && (
            <div className="links">
              <h2>→ 参照先</h2>
              <ul>
                {it.links.map((l) => (
                  <li key={l.relation}>
                    <span className="dim">{linkTitle(l)}</span>
                    <span className={`rel rel-${l.relation_type}`}>{l.relation_type}</span>
                    {l.state === LinkState.null && <span className="null" title="参照の列が NULL">—</span>}
                    {l.state === LinkState.missing && (
                      <span className="failure small" title="参照の列に値があるのに、参照先の行が無い">
                        参照先の行が無い（{shortName(l.target_name)}）
                      </span>
                    )}
                    {l.state === LinkState.found && l.target !== undefined && (
                      <button className="chip" onClick={() => l.target !== undefined && open(l.target)}>
                        {l.target.label} <span className="dim">{shortName(l.target_name)}</span>
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {it.related.map((g) => {
            const title = relatedTitle(g);
            const target = known.get(g.table);
            return (
              <div key={`${g.kind}/${g.via_table ?? ""}/${g.relation}`} className="group">
                <h2>
                  <span className="mark">{title.mark}</span> {title.name}
                  <span className="dim"> {title.detail} · {g.rows.length}{g.truncated ? "+" : ""}</span>
                  {g.missing > 0 && <span className="failure small"> 先の行が無い {g.missing}</span>}
                  {target !== undefined && (
                    <button className="link small" onClick={() => onOpen({ table: g.table, key: null })}>一覧 →</button>
                  )}
                </h2>
                <ul>
                  {g.rows.map((r) => (
                    <li key={r.key.join("/")}>
                      <button className="row-link" onClick={() => open(r)}>
                        <span className="label">{r.label}</span>
                        <span className="dim cells">
                          {r.cells.filter((c) => c.value !== undefined).slice(0, 3)
                            .map((c) => `${c.column} ${c.value ?? ""}`).join(" · ")}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {it.links.length === 0 && it.related.length === 0 && <p className="dim">近隣は無い</p>}
          {it.omitted.length > 0 && (
            <details className="omitted">
              <summary className="dim">たどらなかった関係 {it.omitted.length}</summary>
              <ul>{it.omitted.map((o) => (
                <li key={`${o.table}/${o.relation}`}>{shortName(o.table)} <span className="dim">{o.reason}</span></li>
              ))}</ul>
            </details>
          )}
        </section>
      </div>
    </article>
  );
}

function ErrorText({ error }: { error: unknown }) {
  return <p className="failure pad">{error instanceof ApiError ? error.message : String(error)}</p>;
}
