"""列の SQL の型族と、意味の値の型の両立（どの型族の列に、どの値の型の意味型を当ててよいか）。

Domain 層。モデラー（規則、LLM、同じ意味の伝播）に共通の規則。
"""

from mandara.om.encoding_traits import ENCODING_TARGETS
from mandara.om.model import TypeFamily, ValueType

COMPATIBLE: dict[str, frozenset[str]] = {
    TypeFamily.integer.value: frozenset({"identifier", "code", "enum", "integer", "quantity", "money", "ratio"}),
    TypeFamily.decimal.value: frozenset({"money", "quantity", "ratio"}),
    TypeFamily.float.value: frozenset({"money", "quantity", "ratio"}),
    TypeFamily.text.value: frozenset({"identifier", "code", "enum", "text"}),
    TypeFamily.boolean.value: frozenset({"enum"}),
    TypeFamily.date.value: frozenset({"date"}),
    TypeFamily.time.value: frozenset({"datetime"}),
    TypeFamily.timestamp.value: frozenset({"date", "datetime"}),
    TypeFamily.binary.value: frozenset({"geo.point", "media.image", "media.audio", "media.document"}),
    TypeFamily.uuid.value: frozenset({"identifier"}),
    TypeFamily.xml.value: frozenset({"document.xml"}),
    TypeFamily.json.value: frozenset({"text"}),
    TypeFamily.other.value: frozenset(v.value for v in ValueType),
}
"""列の SQL の型族 → 当ててよい値の型。"""


class IncompatibleEncodingError(ValueError):
    """列の encoding の変換先の型を、列の型族に当てられない（型族と encoding が矛盾している）。"""


def assignable(family: str, encoding: str | None) -> frozenset[str]:
    """列に当ててよい値の型。encoding（OM の列のもの）があれば、その変換先の型だけ（仕様 4.5 の ENCODING_MISMATCH）。"""
    allowed = COMPATIBLE[family]
    if encoding is None:
        return allowed
    target = ENCODING_TARGETS.get(encoding)
    if target is None:
        raise ValueError(f"①に無い encoding: {encoding!r}")
    if target not in allowed:
        raise IncompatibleEncodingError(f"encoding {encoding} の変換先 {target} を、型族 {family} の列に当てられない")
    return frozenset({target})


UNIT_BEARING = frozenset({ValueType.quantity.value, ValueType.money.value})
"""単位が要る値の型（SEMANTIC_TYPE_SHAPE）。"""
