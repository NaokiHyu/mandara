// 関係のグラフ（Cytoscape.js）。表がノード、関係・辺がエッジ。
// 線の形と色は vocabulary.ts の表（関係型ごと、網羅は型で強制）から、色の値は theme.css のトークンから読む。
import cytoscape, { type Core, type ElementDefinition, type StylesheetJson } from "cytoscape";
import { useEffect, useRef } from "react";
import type { GraphEdge, GraphNode } from "./graph";
import type { RelationType } from "./om/model";
import { RELATION } from "./vocabulary";

/** コンテキストの色のトークンの数。これより多いコンテキストは色が巡回する（凡例に出すので、見えない衝突にはしない）。 */
export const CONTEXT_COLORS = 6;

export function contextColor(index: number): string {
  return `--ctx-${index % CONTEXT_COLORS}`;
}

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
        shape: "round-rectangle", width: "label", height: 18, padding: "6px",
        "background-color": t("--panel"), "border-width": 1, "border-color": t("--line-strong"),
        label: "data(label)", color: t("--text"), "font-family": t("--font"), "font-size": 10,
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
    { selector: "node:selected", style: { "border-width": 2, "border-color": t("--accent"), color: t("--text-strong") } },
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
      ...nodes.map((n) => ({ data: { ...n, colorIndex: n.contextIndex % CONTEXT_COLORS } })),
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
      <ul className="legend" aria-label="凡例">
        {contexts.map((c, i) => (
          <li key={c}><span className="swatch" style={{ borderColor: `var(${contextColor(i)})` }} />{c}</li>
        ))}
        {(Object.entries(RELATION) as [RelationType, (typeof RELATION)[RelationType]][]).map(([type, s]) => (
          <li key={type}><span className="line" style={{ borderTopColor: `var(${s.color})`, borderTopStyle: s.line }} />{type}</li>
        ))}
      </ul>
    </div>
  );
}

function applySelection(instance: Core, selected: string | null): void {
  instance.nodes().unselect();
  if (selected !== null) instance.getElementById(selected).select();
}
