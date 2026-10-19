"""宣言の無い参照の推定（規則。T2.2 の 3a）：値の包含の測定（included_in）から、参照とみなす組を選ぶ。

Domain 層の純粋な関数。入力は主張だけ。仕様 6.7.2 B（包含・被覆）、C（名前）、D（大域整合の最小版）。
- 候補は、包含が 100%（参照元の NULL でない異なる値が、すべて参照先のキーにある）の組だけ
- 名前の手がかりの強さ（name_strength）が 1 以上の組だけを選ぶ（名前の無い偶然の包含は、規則では決めない。判断モデルの 3b へ）
- 選び方の順と、値の集合が同じ表どうしの扱いは choose に書く
- 確信度は名前の強さで決める（宣言された外部キーより低い。人のレビューに回る）
- 形の崩れた主張（included_in に target や数が無い）は、黙って候補から外さずにエラーにする（主張の検証を通ったはず）
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NewType

from mandara.om.model import Claim, ClaimPredicate, Table

MODELER = "modeler-joins 0.1"
"""値の包含から推定した参照（宣言の無い外部キー）の提案者。選び方（choose）を変えたら版を上げる。"""
CONFIDENCE = {3: 0.85, 2: 0.8, 1: 0.7}
"""名前の強さ → 推定した参照の確信度。"""
_SUFFIXES = ("id", "code", "key", "no", "number")

InferenceClaims = NewType("InferenceClaims", tuple[Claim, ...])
"""推論（判断モデルと LLM）に渡す表の主張。値の包含の測定（included_in）は、参照とみなして関係の根拠にしたものだけが
残っている（claims_for が作る）。included_in を読む関数はこの型だけを受け取る（絞っていない測定を「外部キー」として渡さない）。"""


def _norm(name: str) -> str:
    """名前の正規化：互換文字を揃え（NFKC）、大文字小文字を畳み、文字と数字だけを残す（日本語の名前も残る。顧客ID → 顧客id）。"""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", name).casefold())


def name_strength(column: str, target_table: str, target_column: str) -> int:
    """列の名前が、参照先（表と列）を指していそうな強さ。
    3：キーの列と同じ名前（productid → product.productid）
    2：キーの列の名前で終わる（billtoaddressid → address.addressid）、または「表の名前 + id / code など」
       （personid → person.businessentityid）
    1：表の名前を含む（productassemblyid → product）
    0：手がかりなし"""
    c, t, k = _norm(column), _norm(target_table), _norm(target_column)
    if not c or not t or not k:
        return 0
    if c == k:
        return 3
    if c.endswith(k) or any(c == t + s for s in _SUFFIXES):
        return 2
    if t in c:
        return 1
    return 0


Join = tuple[tuple[str, str], tuple[tuple[str, str], ...], tuple[str, str]]
"""結合の同一性：（（スキーマ, 表）、（列, 参照先の列）の組の並び、（参照先のスキーマ, 参照先の表））。"""


def join_of(schema: str, table: str, columns: Sequence[str], target_schema: str, target_table: str,
            target_columns: Sequence[str]) -> Join:
    """結合の同一性。宣言された外部キーと推定した参照が同じ結合かを、これ1つで比べる（列の組は対応を保って並べる）。"""
    if len(columns) != len(target_columns) or not columns:
        raise ValueError(f"結合の列の組が不正: {list(columns)} → {list(target_columns)}")
    return (schema, table), tuple(sorted(zip(columns, target_columns, strict=True))), (target_schema, target_table)


def claim_join(claim: Claim) -> Join:
    """参照の主張（references か included_in）の結合。"""
    if claim.target is None:
        raise ValueError(f"参照の主張に target が無い（主張の検証を通ったはず）: {claim.id}")
    return join_of(claim.subject.schema_name, claim.subject.table, claim.columns or [], claim.target.schema_name,
                   claim.target.table, claim.target_columns or [])


def shared_key_candidates(claims: Sequence[Claim]) -> list[Claim]:
    """主キーを共有する参照の被覆（is-a の手がかり）を測る候補：包含が 100% で、両側の列の名前が同じ included_in。
    選び方（choose）に依らず、事実だけで決める（主張台帳の中身を、推定の規則の版に依存させない）。主キーを共有する形かは
    呼ぶ側（mandara.extractors.values.shared_keys）が表の主キーで確かめる。"""
    out: list[Claim] = []
    for c in claims:
        if c.predicate != ClaimPredicate.included_in.value:
            continue
        m = _measured(c)
        if m.distinct > 0 and m.matched == m.distinct and _norm(m.column) == _norm(m.key):
            out.append(c)
    return out


@dataclass(frozen=True)
class Chosen:
    claim: Claim
    """選んだ included_in の主張（関係の根拠になる）。"""
    strength: int
    confidence: float
    one_to_one: bool
    """参照元の値が重ならない（1 つの値の行が 1 行）。"""


@dataclass(frozen=True)
class _Measured:
    """included_in の主張の、形を確かめた中身。"""

    claim: Claim
    table: str
    column: str
    target: str
    target_table: str
    key: str
    distinct: int
    matched: int
    key_values: int
    max_repeats: int

    @property
    def same_set(self) -> bool:
        """参照元の値の集合と、参照先のキーの値の集合が同じ（互いに包含）。"""
        return self.matched == self.distinct == self.key_values


def _measured(claim: Claim) -> _Measured:
    t, columns, keys = claim.target, claim.columns or [], claim.target_columns or []
    counts = (claim.distinct_values, claim.matched_values, claim.target_values, claim.max_repeats)
    if t is None or len(columns) != 1 or len(keys) != 1 or None in counts:
        raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {claim.id}")
    distinct, matched, key_values, repeats = [n for n in counts if n is not None]  # 上で None が無いことを確かめた
    return _Measured(claim, f"{claim.subject.schema_name}.{claim.subject.table}", columns[0],
                     f"{t.schema_name}.{t.table}", t.table, keys[0], distinct, matched, key_values, repeats)


def own_keys(claims: Sequence[Claim]) -> dict[str, tuple[str, ...]]:
    """表 → 規則が主キーにする組：宣言された主キー。無ければ、データで一意な組が 1 つだけのときのその組
    （mandara.modeler.rules と同じ。T2.7）。判断モデルが選ぶ主キーは、この段（抽出のすぐ後の規則）ではまだ無い。"""
    declared: dict[str, tuple[str, ...]] = {}
    in_data: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for c in claims:
        name = f"{c.subject.schema_name}.{c.subject.table}"
        if c.predicate == ClaimPredicate.identified_by.value:
            declared[name] = tuple(c.columns or [])
        elif c.predicate == ClaimPredicate.unique_in_data.value:
            in_data[name].append(tuple(c.columns or []))
    return {**{n: u[0] for n, u in in_data.items() if len(u) == 1}, **declared}


def choose(claims: Sequence[Claim]) -> list[Chosen]:
    """参照とみなす included_in の主張（表と列の順）。

    1. 候補：包含が 100% で、名前の手がかりが 1 以上の組。ただし表の 1 列の主キーが別の表を指すのは、名前が完全に一致する
       ときだけ（addresstype.addresstypeid → address のように、主キーの名前が別の表の名前を含むだけの偶然を除く）
    2. 列ごとに並べる：名前の強さ → 参照先のキーの値が少ない（最も狭い包含先。is-a の連なりのすぐ上、同じ集合の表を含む）
       → ハブ（名前の手がかりのある完全な包含で、より多くの表から指される表。値の集合が同じ表どうしを見分ける）→ 表の名前
    3. 値の集合が同じ表どうし（互いに包含）が両向きに選ばれたら、ハブの弱い側を指していた列は、その候補を捨てて次を選ぶ
       （2 本の循環を作らない。例：person と password はどちらも同じ人の集合で、person のほうが多くの表から指される）
    """
    keys = own_keys(claims)
    by_column: dict[tuple[str, str], list[tuple[_Measured, int]]] = defaultdict(list)
    for c in claims:
        if c.predicate != ClaimPredicate.included_in.value:
            continue
        m = _measured(c)
        if m.distinct == 0 or m.matched != m.distinct:
            continue
        strength = name_strength(m.column, m.target_table, m.key)
        own_key = keys.get(m.table) == (m.column,)
        if strength >= 1 and (not own_key or strength == 3):
            by_column[(m.table, m.column)].append((m, strength))
    hub: dict[str, int] = defaultdict(int)
    for options in by_column.values():
        for target in {m.target for m, strength in options if strength >= 2}:
            hub[target] += 1
    ranked = {key: sorted(options, key=lambda o: (-o[1], o[0].key_values, -hub[o[0].target], o[0].target))
              for key, options in by_column.items()}
    while True:
        picked = {key: options[0] for key, options in ranked.items() if options}
        same = {(m.table, m.target): key for key, (m, _) in picked.items() if m.same_set}
        losers = [key for (source, target), key in same.items()
                  if (target, source) in same and (hub[target], target) < (hub[source], source)]
        if not losers:
            break
        for key in losers:
            ranked[key] = ranked[key][1:]
    return [Chosen(m.claim, strength, CONFIDENCE[strength], m.max_repeats == 1)
            for _, (m, strength) in sorted(picked.items())]


def claims_for(table: Table, table_claims: Sequence[Claim]) -> InferenceClaims:
    """推論（判断モデルと LLM）に渡す表の主張：値の包含の測定（included_in）は、表の関係の根拠になっているもの（参照と
    みなしたもの）だけを残す。後段は、宣言された外部キー（references）と残った included_in を、同じ「参照」として扱う
    （mandara.modeler.classify.references）。参照とみなさなかった測定は、推論の材料にしない（候補が多すぎ、偶然の包含を含む）。
    データで一意な組（unique_in_data。T2.7）も、表の主キーに選んだものだけを残す（後段は宣言の主キーと同じに扱う。
    mandara.modeler.classify.primary_key）。"""
    evidence = {e for r in table.relations or [] for e in r.evidence or []}
    primary = frozenset(table.identity.primary) if table.identity is not None else None
    return InferenceClaims(tuple(c for c in table_claims
                                 if (c.predicate != ClaimPredicate.included_in.value or c.id in evidence)
                                 and (c.predicate != ClaimPredicate.unique_in_data.value
                                      or frozenset(c.columns or []) == primary)))
