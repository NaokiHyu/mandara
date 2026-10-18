// ドメインビューワ（承認済みの OM から。仕様 7.6）：ドメイン（集合。表のイメージ）とインスタンス（個。行のイメージ）。
// relation の表は出さずにリンクとしてたどる。値はソースをその場で読む（ゼロコピー）。読めなかったら理由をそのまま見せる。
import { useEffect, useMemo, useState } from "react";
import { ApiError, getDomainRows, getDomains, getInstance } from "./api";
import { emphasized, extendTrail, groupKey, kpisOf, linkTexts, linkTitle, type Place, relatedTitle, rowKey, samePlace,
         type Within } from "./domain";
import { amountText } from "./groupview";
import { asKind, KIND } from "./vocabulary";
import { GroupPanel, Links, Series, Timeline, Totals } from "./GroupPanel";
import { DomainIcon, Label } from "./parts";
import { contextColor, shortName } from "./graph";
import { type DomainIndex, type DomainRows, type DomainSummary, type Instance, type InstanceSummary,
         GroupView, LinkState, type RelatedGroup, RelatedKind } from "./om/model";

type Load<T> = { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: unknown };

const SEARCH_DELAY_MS = 250;
const count = new Intl.NumberFormat("en-US");

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
  const { byTable, color } = palette(index.value);
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
        <DomainList domains={domains} filter={filter} selected={place?.table ?? null} color={color}
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
        <Trail trail={trail} byTable={byTable} current={place} color={color} onGo={go} />
        {place === null || current === undefined ? (
          <div className="done">
            <div className="done-mark">◆</div>
            <p>{place === null ? "ドメインを選ぶ" : `${place.table} はドメインではない`}</p>
            <p className="dim">承認済みの domain / event の表がドメイン。relation の表はリンクとしてたどる</p>
          </div>
        ) : place.key === null ? (
          <Collection key={`${current.table}/${JSON.stringify(place.within ?? null)}`} ontologyId={ontologyId}
                      domain={current} within={place.within} known={byTable} color={color} onOpen={go} />
        ) : (
          <InstancePane key={`${current.table}/${place.key.join("/")}`} ontologyId={ontologyId} domain={current}
                        instanceKey={place.key} known={byTable} color={color} onOpen={go} />
        )}
      </section>
    </div>
  );
}

/** ドメインの表引きと、ドメインのカテゴリ（コンテキスト）の色（CSS 変数の名前）。ドメインでない表は地味な色。地図と共通。 */
export function palette(index: DomainIndex): { byTable: Map<string, DomainSummary>; color: (table: string) => string } {
  const byTable = new Map(index.domains.map((d) => [d.table, d]));
  const colors = new Map(index.contexts.map((c, i) => [c, contextColor(i)]));
  const color = (table: string) => {
    const d = byTable.get(table);
    if (d === undefined) return "--text-dim";
    const found = colors.get(d.context);
    if (found === undefined) throw new Error(`ドメインのコンテキスト ${d.context} が DomainIndex.contexts に無い（契約の誤り）`);
    return found;
  };
  return { byTable, color };
}

function DomainList({ domains, filter, selected, color, onOpen }: {
  domains: DomainSummary[];
  filter: string;
  selected: string | null;
  color: (table: string) => string;
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
        <section key={context} style={{ borderLeftColor: `var(${color(group[0]?.table ?? "")})` }}>
          <h2 style={{ color: `var(${color(group[0]?.table ?? "")})` }}>{context}</h2>
          <ul>
            {group.map((d) => (
              <li key={d.table}>
                <button className={`table${d.table === selected ? " selected" : ""}`} aria-pressed={d.table === selected}
                        onClick={() => onOpen(d.table)}>
                  <DomainIcon kind={d.kind} color={color(d.table)} />
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

function Trail({ trail, byTable, current, color, onGo }: {
  trail: Place[];
  byTable: Map<string, DomainSummary>;
  current: Place | null;
  color: (table: string) => string;
  onGo: (p: Place) => void;
}) {
  if (trail.length === 0) return null;
  return (
    <nav className="trail" aria-label="たどった道">
      {trail.map((p, i) => (
        <span key={`${p.table}/${(p.key ?? []).join("/")}/${i}`}>
          {i > 0 && <span className="dim sep">›</span>}
          <button className={`link${current !== null && samePlace(p, current) ? " on" : ""}`} onClick={() => onGo(p)}>
            <span className="dot" style={{ background: `var(${color(p.table)})` }} />
            {p.within !== undefined && `${shortName(byTable.get(p.within.table)?.name ?? p.within.table)} #${p.within.key.join("·")} の `}
            {shortName(byTable.get(p.table)?.name ?? p.table)}{p.key === null ? "" : ` #${p.key.join("·")}`}
          </button>
        </span>
      ))}
    </nav>
  );
}

/** 集合（表のイメージ）：検索窓と結果。結果の行を開くとインスタンス。 */
function Collection({ ontologyId, domain, within, known, color, onOpen }: {
  ontologyId: string;
  domain: DomainSummary;
  within: Within | undefined;
  known: Map<string, DomainSummary>;
  color: (table: string) => string;
  onOpen: (p: Place) => void;
}) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Load<DomainRows>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      setRows({ state: "loading" });
      getDomainRows(ontologyId, domain.table, query, within).then(
        (value) => { if (live) setRows({ state: "ok", value }); },
        (error: unknown) => { if (live) setRows({ state: "error", error }); });
    }, query === "" ? 0 : SEARCH_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [ontologyId, domain.table, query, within]);
  const linked = linkTexts(rows.state === "ok" ? rows.value.links : undefined);  // n:m で絞ったとき、つながりの値を行に添える

  return (
    <article className="collection" aria-label="ドメイン">
      <header className="domain-head">
        <DomainIcon kind={domain.kind} color={color(domain.table)} big />
        {rows.state === "ok" && rows.value.within !== undefined && (
          <>
            <button className="chip within" onClick={() => rows.state === "ok" && rows.value.within !== undefined
              && onOpen({ table: rows.value.within.table, key: rows.value.within.key })}>
              {known.get(rows.value.within.table) !== undefined && (
                <DomainIcon kind={known.get(rows.value.within.table)?.kind ?? ""} color={color(rows.value.within.table)} />
              )}
              <span className="key">{rows.value.within.label}</span>
            </button>
            <span className="dim">の</span>
          </>
        )}
        <h1>{shortName(domain.name)}</h1>
        <span className="context-tag" style={{ color: `var(${color(domain.table)})` }}>{domain.context}</span>
        <span className="dim">{domain.name}</span>
        {within !== undefined && (
          <button className="link small" onClick={() => onOpen({ table: domain.table, key: null })}>すべての {shortName(domain.name)} →</button>
        )}
      </header>
      {domain.grain !== undefined && <p className="grain dim">{domain.grain}</p>}
      <input className="search" autoFocus placeholder={domain.searchable.length > 0
        ? `検索：${domain.searchable.slice(0, 4).join(", ")}${domain.searchable.length > 4 ? " …" : ""} とキー`
        : "検索：キー"} value={query} onChange={(e) => setQuery(e.target.value)} />
      {rows.state === "loading" && <p className="dim">searching…</p>}
      {rows.state === "error" && <ErrorText error={rows.error} />}
      {rows.state === "ok" && rows.value.view !== GroupView.list && (
        <div className={`group collection-profile view-${rows.value.view}`}
             style={{ borderLeftColor: `var(${color(domain.table)})` }}>
          {rows.value.view === GroupView.time_series && (
            <Series group={rows.value} color={color(domain.table)} chartId={`collection-${domain.table}`} />
          )}
          <Totals group={rows.value} />
          {rows.value.view === GroupView.timeline && (
            <Timeline group={rows.value} color={color(domain.table)} onOpen={(r) => onOpen({ table: r.table, key: r.key })}
                      note="期間の帯は、表示中の行（検索と上限で絞った行）について" />
          )}
        </div>
      )}
      {rows.state === "ok" && rows.value.links !== undefined && (
        <div className="collection-links">
          <Links links={rows.value.links} color={color(domain.table)} onOpen={(r) => onOpen({ table: r.table, key: r.key })}
                 chartId={`collection-links-${domain.table}`} />
        </div>
      )}
      {rows.state === "ok" && (
        <>
          <p className="dim small">全 {count.format(rows.value.count)} 件{query.trim() !== "" ? ` · 検索 ${rows.value.rows.length}${rows.value.truncated ? "+" : ""} 件` : ""}
            {" · "}{rows.value.rows.length}{rows.value.truncated ? "+" : ""} 件を表示
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
                  <td className="key">{r.key.join("·")}</td>
                  <td><Label summary={r} />
                    {linked.has(rowKey(r.key)) && <span className="link-cells"> {linked.get(rowKey(r.key))}</span>}</td>
                  {r.cells.map((c) => <td key={c.column} className={emphasized(domain, c.column) ? "key" : undefined}>
                    {c.value ?? <span className="null">—</span>}</td>)}
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
  if (domain.label_columns.length === 0 && domain.label_from !== undefined) {
    return "見出しの列が無いので、is-a の上位の見出しを借りている";
  }
  return domain.label_columns.length === 0 ? "見出しの列が無いので、キーを見出しにしている"
    : `見出しは ${domain.label_columns.join(" + ")}（列の名前からの推定）`;
}

/** 個（行のイメージ）：値、参照先へのリンク、近隣の集まり。ドメインビューワと地図の Drawer が使う。 */
export function InstancePane({ ontologyId, domain, instanceKey, known, color, onOpen }: {
  ontologyId: string;
  domain: DomainSummary;
  instanceKey: readonly string[];
  known: Map<string, DomainSummary>;
  color: (table: string) => string;
  onOpen: (p: Place) => void;
}) {
  const [shown, setShown] = useState<Load<Instance>>({ state: "loading" });
  const [picked, setPicked] = useState<string | null>(null); // 選んだタブ（近隣の集まりの鍵）。無ければ先頭
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
  // 「この集合 →」：このインスタンスで絞った集合（部分集合）を開く。n:m は relation の表を挟んで絞る。
  // n:m なのに via_relation が無い（契約に反する）なら開かない（1:n として開くと別の関係で絞ってしまう）
  const listOf = (g: RelatedGroup): Place | null => {
    if (g.kind !== RelatedKind.via) return { table: g.table, key: null, within: { table: it.table, key: it.key, relation: g.relation } };
    if (g.via_relation === undefined) return null;
    return { table: g.table, key: null, within: { table: it.table, key: it.key, relation: g.relation, via: g.via_relation } };
  };
  const onList = (g: RelatedGroup) => { const p = listOf(g); return p === null ? null : () => onOpen(p); };
  // 時系列のまとまりを先頭に（数字の帯とタブの先頭に来る）。ほかは API の順のまま
  const groups = [...it.related.filter((g) => g.view === GroupView.time_series),
                  ...it.related.filter((g) => g.view !== GroupView.time_series)];
  const tab = groups.find((g) => groupKey(g) === picked) ?? groups[0];
  const isA = it.links.filter((l) => l.relation_type === "is-a" && l.target !== undefined);
  return (
    <article className="instance object" aria-label="インスタンス">
      <header className="object-head">
        <div className="object-kicker">
          <span className="ctx-dot" style={{ background: `var(${color(domain.table)})` }} />
          <span>{domain.context}</span>
          <span>· {KIND[asKind(domain.kind)].text}</span>
          {isA.map((l) => (
            <button key={l.relation} className="link small" onClick={() => l.target !== undefined && open(l.target)}>
              · is-a {shortName(l.target_name)}
            </button>
          ))}
        </div>
        <div className="object-title">
          <DomainIcon kind={domain.kind} color={color(domain.table)} big />
          <h1 className="key">{it.label}</h1>
        </div>
        <div className="object-sub">
          <button className="link" style={{ color: `var(${color(domain.table)})` }}
                  onClick={() => onOpen({ table: domain.table, key: null })}>{shortName(it.name)}</button>
          <span className="key">#{it.key.join("·")}</span>
          {it.links.filter((l) => l.relation_type !== "is-a" && l.target !== undefined).slice(0, 3).map((l) => (
            <span key={l.relation} className="dim">· {linkTitle(l)} <span className="soft">{l.target?.label}</span></span>
          ))}
        </div>
      </header>
      {groups.length > 0 && (
        <div className="kpi-strip" role="list" aria-label="近隣の集まりの数">
          {kpisOf(groups).map((k) => (
            <button key={k.key} role="listitem" className={`kpi-cell${tab !== undefined && groupKey(tab) === k.key ? " on" : ""}`}
                    onClick={() => setPicked(k.key)}>
              <span className="eyebrow">{k.mark} {k.label}</span>
              <span className="kpi-big">{count.format(k.count)}</span>
              <span className="kpi-note">{k.measure === undefined ? "件"
                : `Σ${k.measure.column} ${amountText(k.measure.value, k.measure.unit, true)}`}</span>
            </button>
          ))}
        </div>
      )}
      <div className="object-body">
        <section className="object-main neighbors" aria-label="近隣">
          {groups.length > 0 && (
            <nav className="tabs" aria-label="近隣の集まり">
              {groups.map((g) => (
                <button key={groupKey(g)} className={tab === g ? "on" : ""} aria-pressed={tab === g}
                        onClick={() => setPicked(groupKey(g))}>
                  {relatedTitle(g, it.related).mark} {shortName(g.name)} <span className="dim">{count.format(g.count)}</span>
                </button>
              ))}
            </nav>
          )}
          {tab !== undefined && (
            <GroupPanel key={groupKey(tab)} group={tab} siblings={it.related} target={known.get(tab.table)}
                        color={color(tab.table)} onOpen={open} onList={onList(tab)} />
          )}
          {groups.length === 0 && <p className="dim">近隣の集まりは無い</p>}
        </section>
        <aside className="object-side" aria-label="値と参照先">
          <div className="eyebrow">プロパティ</div>
          <table className="props">
            <tbody>
              {it.fields.map((f) => (
                <tr key={f.column} className={emphasized(domain, f.column) ? "key" : undefined}>
                  <th title={f.semantic_type ?? ""}>{f.column}</th>
                  <td>{f.value ?? <span className="null">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {it.links.length > 0 && (
            <>
              <div className="eyebrow">参照先</div>
              <ul className="refs">
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
                        {known.get(l.target_table) !== undefined && (
                          <DomainIcon kind={known.get(l.target_table)?.kind ?? ""} color={color(l.target_table)} />
                        )}
                        <Label summary={l.target} /> <span className="dim">{shortName(l.target_name)}</span>
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {it.omitted.length > 0 && (
            <details className="omitted">
              <summary className="dim">たどらなかった関係 {it.omitted.length}</summary>
              <ul>{it.omitted.map((o) => (
                <li key={`${o.table}/${o.relation}`}>{shortName(o.table)} <span className="dim">{o.reason}</span></li>
              ))}</ul>
            </details>
          )}
        </aside>
      </div>
    </article>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  return <p className="failure pad">{error instanceof ApiError ? error.message : String(error)}</p>;
}
