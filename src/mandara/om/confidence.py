"""表の確信度の内訳（TableConfidence）と、表の confidence の関係（仕様 4.4、4.5）。

Domain 層。I/O を持たない。
- 表の confidence は、書かれた部品（所属・種類・主キー）の最小値。部品を足したり変えたりするときは、ここで confidence も揃える
- 部品の名前は TableConfidence の属性名。レビューと較正は部品ごとに見る
"""

from mandara.om.model import Table, TableConfidence

PARTS = tuple(TableConfidence.model_fields)
"""部品の名前（entity_group / kind / identity）。"""


def present(parts: TableConfidence) -> dict[str, float]:
    """書かれた部品だけ（名前 → 確信度）。"""
    return {name: value for name in PARTS if (value := getattr(parts, name)) is not None}


def overall(parts: TableConfidence) -> float:
    """表の confidence：書かれた部品の最小値。部品が 1 つも無ければ決まらない（呼ぶ側の誤り）。"""
    values = present(parts)
    if not values:
        raise ValueError("確信度の部品が 1 つも無い")
    return min(values.values())


def with_part(table: Table, name: str, confidence: float | None) -> Table:
    """表の確信度の部品を 1 つ置き換え（None なら外し）、confidence を部品の最小値に揃える。"""
    if name not in PARTS:
        raise ValueError(f"確信度の部品ではない: {name}")
    if table.confidence_parts is None:
        raise ValueError(f"確信度の内訳が無い表の部品は変えない: {table.name}")
    parts = table.confidence_parts.model_copy(update={name: confidence})
    return table.model_copy(update={"confidence_parts": parts, "confidence": overall(parts)})
