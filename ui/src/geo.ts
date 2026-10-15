// OM から、地図に出せる列（意味の型が geo.point の列）を見つける。
import { type OntologyDocument, ValueType } from "./om/model";

export interface GeoColumn {
  tableId: string;
  tableName: string;
  column: string;
}

export function geoColumns(doc: OntologyDocument): GeoColumn[] {
  const domains = doc.vocabulary.column_domains ?? {};
  return doc.tables.flatMap((t) =>
    Object.values(t.columns ?? {})
      .filter((c) => c.domain !== undefined && domains[c.domain]?.type === ValueType.geoFULL_STOPpoint)
      .map((c) => ({ tableId: t.id, tableName: t.name, column: c.name })),
  );
}
