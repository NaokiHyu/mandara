// 関係のグラフ（Cytoscape.js）。表がノード、関係・辺がエッジ。
// 線の形と色は vocabulary.ts の表（関係型ごと、網羅は型で強制）から、色の値は theme.css のトークンから読む。
import cytoscape, { type Core, type ElementDefinition, type StylesheetJson } from "cytoscape";
import { useEffect, useRef } from "react";
import { CONTEXT_COLORS, contextColor, type GraphEdge, type GraphNode } from "./graph";
import type { RelationType } from "./om/model";
import { asKind, KIND, RELATION } from "./vocabulary";


function token(style: CSSStyleDeclaration, name: string): string {
  const value = style.getPropertyValue(name).trim();
  if (value === "") throw new Error(`theme.css にトークン ${name} が無い`);
  return value;
}

function stylesheet(contextCount: number): StylesheetJson {
  const style = getComputedStyle(document.documentElement);
  const t = (name: string) => token(style, name);
  const relationStyles = (Object.entries(RELATION) as [RelationType, (typeof RELATION)[RelationType]][]).map(
    ([type, s]) => ({
      selector: `edge[type = "${type}"]`, // 語彙の値（閉じた集合）なので、組み立てても注入にならない
      style: {
        "line-color": t(s.color), "target-arrow-color": t(s.color), "line-style": s.line,
        "target-arrow-shape": s.arrow, "target-arrow-fill": s.hollow ? ("hollow" as const) : ("filled" as const),
      },
    }),
  );
  return [
    {
      selector: "node",
      style: {
        shape: "rectangle", width: "label", height: 18, padding: "6px",
        "background-color": t("--panel-raised"), "border-width": 1, "border-color": t("--line-strong"),
        label: "data(display)", color: t("--text"), "font-family": t("--font"), "font-size": 11,
        "text-valign": "center", "text-halign": "center",
      },
    },
    ...Array.from({ length: Math.min(contextCount, CONTEXT_COLORS) }, (_, i) => ({
      selector: `node[colorIndex = ${i}]`,
      style: { "border-color": t(contextColor(i)) },
    })),
    { selector: "node[status = 'proposed']", style: { "border-style": "dashed" } },
    { selector: "node[status = 'rejected']", style: { opacity: 0.35 } },
    { selector: "node[!kind]", style: { color: t("--text-dim") } },
    { selector: "node[?derived]", style: { "font-style": "italic" } },
    { selector: "node:selected", style: { "border-width": 2, "border-color": t("--text-key"), color: t("--text-key"),
                                          "underlay-color": t("--accent"), "underlay-opacity": 0.25, "underlay-padding": 5 } },
    // 選んだ表の近く（隣の表と、そこへの関係）だけを明るく残す
    { selector: ".faded", style: { opacity: 0.28 } },
    { selector: "edge.hot", style: { width: 2, label: "data(type)", color: t("--text-soft"), "font-size": 9,
                                     "font-family": t("--font"), "text-background-color": t("--bg"),
                                     "text-background-opacity": 1, "text-background-padding": "2px" } },
    {
      selector: "edge",
      style: {
        width: 1, "curve-style": "bezier", "arrow-scale": 0.7, "loop-direction": "-45deg", "loop-sweep": "-60deg",
      },
    },
    ...relationStyles,
    {
      // 中間表の辺：関係型の線に、太さと破線とラベルを重ねる
      selector: "edge[via = 'edge']",
      style: { width: 2, "line-style": "dashed", label: "data(label)", color: t("--text-dim"), "font-size": 9,
               "font-family": t("--font") },
    },
    { selector: "edge[status = 'rejected']", style: { opacity: 0.25 } },
  ];
}

export function GraphView({ nodes, edges, contexts, selected, onSelect }: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  contexts: string[];
  selected: string | null;
  onSelect: (tableId: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  useEffect(() => {
    if (container.current === null) return;
    const elements: ElementDefinition[] = [
      ...nodes.map((n) => ({ data: { ...n, colorIndex: n.contextIndex % CONTEXT_COLORS,
                                     display: `${n.kind === undefined ? "?" : KIND[asKind(n.kind)].mark}  ${n.label}` } })),
      ...edges.map((e) => ({ data: { ...e } })),
    ];
    const instance = cytoscape({
      container: container.current,
      elements,
      style: stylesheet(contexts.length),
      layout: { name: "cose", animate: false, randomize: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 80 },
      wheelSensitivity: 0.3,
    });
    instance.on("tap", "node", (event) => onSelect(event.target.id() as string));
    instance.on("tap", (event) => { if (event.target === instance) onSelect(null); });
    cy.current = instance;
    applySelection(instance, selectedRef.current); // 作り直したときも、今の選択を当てる
    return () => { instance.destroy(); cy.current = null; };
  }, [nodes, edges, contexts, onSelect]);

  useEffect(() => {
    if (cy.current !== null) applySelection(cy.current, selected);
  }, [selected]);

  return (
    <div className="graph-frame">
      <div ref={container} className="graph" role="img"
           aria-label="表と関係のグラフ。キーボードでは list の表示から表を選ぶ" />
    </div>
  );
}

function countOf(counts: ReadonlyMap<string, number>, context: string): number {
  const n = counts.get(context);
  if (n === undefined) throw new Error(`コンテキスト ${context} の表の数が無い（同じ OM から作っているはず）`);
  return n;
}

/** グラフの凡例（左の帯）：コンテキストと表の数、関係型の線、表の種類。 */
export function GraphLegend({ contexts, counts }: { contexts: readonly string[]; counts: ReadonlyMap<string, number> }) {
  return (
    <aside className="graph-side" aria-label="凡例">
      <div className="eyebrow">コンテキスト</div>
      <ul>
        {contexts.map((c, i) => (
          <li key={c}><span className="sq" style={{ background: `var(${contextColor(i)})` }} /><span className="grow">{c}</span>
            <span className="dim">{countOf(counts, c)}</span></li>
        ))}
      </ul>
      <div className="eyebrow">関係型</div>
      <ul>
        {(Object.entries(RELATION) as [RelationType, (typeof RELATION)[RelationType]][]).map(([type, s]) => (
          <li key={type}><span className="line" style={{ borderTopColor: `var(${s.color})`, borderTopStyle: s.line }} />{type}</li>
        ))}
      </ul>
      <div className="eyebrow">表の種類</div>
      <ul>
        {Object.values(KIND).map((k) => <li key={k.mark}><span className="kind-box">{k.mark}</span>{k.text}</li>)}
        <li className="dim"><span className="kind-box dashed" />提案中（未承認）</li>
      </ul>
    </aside>
  );
}

function applySelection(instance: Core, selected: string | null): void {
  instance.nodes().unselect();
  instance.elements().removeClass("faded hot");
  if (selected === null) return;
  const node = instance.getElementById(selected);
  if (node.empty()) return;
  node.select();
  const near = node.closedNeighborhood();
  instance.elements().not(near).addClass("faded");
  near.edges().addClass("hot");
  instance.animate({ fit: { eles: near, padding: 90 } }, { duration: 450 }); // 選んだ表の近くに寄る
}
