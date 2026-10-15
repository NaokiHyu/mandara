"""列の SQL の型族と、意味の値の型の両立（どの型族の列に、どの値の型のドメインを当ててよいか）。

Domain 層。モデラー（規則、LLM、同じ意味の伝播）に共通の規則。
"""

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

UNIT_BEARING = frozenset({ValueType.quantity.value, ValueType.money.value})
"""単位が要る値の型（DOMAIN_SHAPE）。"""
