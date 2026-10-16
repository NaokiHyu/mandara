// OM の閉じた語彙（表の種類、関係型）の、UI での表し方。①から生成した enum で網羅を型で強制する
// （語彙が増えれば、ここが型エラーになる）。色は theme.css のトークン名で持つ。
import { RelationType, TableKind } from "./om/model";

export const KIND: Record<TableKind, { mark: string; text: string; color: string }> = {
  [TableKind.domain]: { mark: "D", text: "domain — もの", color: "--accent" },
  [TableKind.relation]: { mark: "R", text: "relation — もの同士の関係", color: "--ok" },
  [TableKind.event]: { mark: "E", text: "event — 出来事", color: "--warn" },
};

export interface LineStyle {
  color: string;
  line: "solid" | "dotted" | "dashed";
  arrow: "triangle" | "diamond";
  hollow: boolean;
}

export const RELATION: Record<RelationType, LineStyle & { text: string }> = {
  [RelationType.is_a]: { text: "is-a — 一種", color: "--text", line: "solid", arrow: "triangle", hollow: true },
  [RelationType.part_of]: { text: "part-of — 部分", color: "--accent", line: "solid", arrow: "diamond", hollow: false },
  [RelationType.classified_as]: { text: "classified-as — 分類", color: "--ok", line: "dotted", arrow: "triangle", hollow: false },
  [RelationType.has]: { text: "has — 参照・属性", color: "--line-strong", line: "solid", arrow: "triangle", hollow: false },
  [RelationType.derived_from]: { text: "derived-from — 導出元", color: "--text-dim", line: "dashed", arrow: "triangle", hollow: false },
};

/** 契約（JSON Schema の enum）で検証済みの値を、生成された enum として読む（生成型では string になっているため）。 */
export function asKind(value: string): TableKind {
  if (!(value in KIND)) throw new Error(`表の種類が語彙に無い: ${value}`);
  return value as TableKind;
}

export function asRelationType(value: string): RelationType {
  if (!(value in RELATION)) throw new Error(`関係型が語彙に無い: ${value}`);
  return value as RelationType;
}
