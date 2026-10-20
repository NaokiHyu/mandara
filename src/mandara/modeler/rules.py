"""モデラー v0：主張から、規則だけで OM を提案する（LLM なし）。

Domain 層の純粋な関数。入力は主張台帳と範囲だけで、カタログや DB を直接読まない。
- 入力の主張は、入口で主張の検証（mandara.om.claims）に通す。形の崩れた主張を黙って空のキーなどに変えない
- 出すものはすべて status: proposed。confidence と evidence（主張 ID）を必ず付ける（仕様 6.3）
- 規則で決まらないものは決めない。表の種類は、仕様の定義にそのまま当たる表にだけ付け、ほかは未決（None）
- 確信度は、その提案の中で最も弱い根拠に合わせる。主張自身の確信度の合成（仕様 6.6.5）は v0 ではしない
- 安定 ID は、存在の場所だけから作る（表はソース・スキーマ・表名、関係はそれに外部キーの制約の場所）。
  ほかの事実（コメント、ON DELETE など）が変わっても ID は変わらない。改名すると変わる（追跡は T6.1。仕様 15.2）
- 出力は検証（仕様 4.5）に通し、違反があればエラーにする
"""

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TypedDict

from mandara.om.claims import check_claims
from mandara.om.confidence import overall
from mandara.om.invariants import Violation, check
from mandara.om.model import (
    Cardinality,
    Claim,
    ClaimPredicate,
    Column,
    ColumnPair,
    HasKind,
    Identity,
    KeyColumns,
    ObjectKind,
    Relation,
    RelationType,
    ReviewStatus,
    Table,
    TableConfidence,
    TableKind,
    Vocabulary,
)
from mandara.om.names import qualify
from mandara.om.ontology import SELF, Ontology
from mandara.modeler import joins

MODELER = "modeler-rules 0.1"

DECLARED_CONFIDENCE = 1.0
"""列が存在すること（カタログの宣言）。"""
ENTITY_GROUP_CONFIDENCE = 0.7
"""エンティティグループをスキーマ名と同じにするのは当て推量（ドメインの意味的な集まりは、ソースの区切りと一致するとは限らない）。"""
KIND_CONFIDENCE = 0.9
"""表の種類の規則は、仕様 4.1 の定義にそのまま当たる（ビューは計算で導出される、中間表は複数の参照の組で識別される）。
ただしソースの命名・使われ方とずれることはあるので 1.0 にはしない。"""
ENCODING_CONFIDENCE = 0.95
"""物理的な表現は、読んだ値（上限までの見本）がすべてそのコーデックで読めたことによる。見本の外の行は読んでいない。"""
REFERENCE_CONFIDENCE = 0.9
"""外部キーは参照を確実に示す。ただし関係型（has / part-of / is-a）は v0 では判定していない。"""

_TableKey = tuple[str, str]


@dataclass(frozen=True)
class Skipped:
    """提案しなかったもの。黙って捨てず、実行の記録に残す。"""

    claim: str
    reason: str


@dataclass(frozen=True)
class Proposal:
    ontology: Ontology
    skipped: tuple[Skipped, ...]


class InvalidInputError(Exception):
    """モデラーの入力（主張台帳と範囲）が不正、または不完全。"""


class InvalidProposalError(Exception):
    """モデラーの出力が検証（4.5）を通らない。モデラーのバグ。"""

    def __init__(self, violations: Sequence[Violation]) -> None:
        lines = "\n".join(f"  - {v.location}: {v.rule.name} {v.detail}" for v in violations)
        super().__init__(f"提案が検証を通らない（{len(violations)} 件）:\n{lines}")
        self.violations = tuple(violations)


def propose(source: str, schemas: Collection[str], claims: Sequence[Claim]) -> Proposal:
    """schemas は台帳を作ったときの範囲。範囲内のスキーマにある表は、台帳にすべてあるはず。"""
    if violations := check_claims(claims):
        raise InvalidInputError(f"入力の主張が不正（{len(violations)} 件）。例 {violations[:3]}")
    if foreign := sorted({c.subject.source for c in claims} - {source}):
        raise InvalidInputError(f"ソース {source} の提案に、別のソースの主張がある: {foreign}")
    if outside := sorted({c.subject.schema_name for c in claims} - set(schemas)):
        raise InvalidInputError(f"範囲外のスキーマの主張がある: {outside}")

    by_table: dict[_TableKey, list[Claim]] = defaultdict(list)
    for claim in claims:
        by_table[(claim.subject.schema_name, claim.subject.table)].append(claim)
    if missing := sorted(key for key, group in by_table.items() if _one(group, ClaimPredicate.declared_as) is None):
        raise InvalidInputError(f"declared_as の主張が無い表がある（台帳が不完全）: {missing}")
    table_ids = {key: "tbl_" + _stable_hash(source, *key) for key in by_table}
    # 宣言の無い参照：値の包含の測定から、参照とみなす組（全体を見て1列に1つ。mandara.modeler.joins）
    inferred: dict[_TableKey, list[joins.Chosen]] = defaultdict(list)
    for chosen in joins.choose(claims):
        inferred[(chosen.claim.subject.schema_name, chosen.claim.subject.table)].append(chosen)

    tables: list[Table] = []
    skipped: list[Skipped] = []
    for key in sorted(by_table):
        table, table_skipped = _table(source, set(schemas), key, by_table[key], table_ids, inferred.get(key, []))
        tables.append(table)
        skipped.extend(table_skipped)

    ontology = Ontology(vocabulary=Vocabulary(), tables=tuple(tables))
    if violations := check(ontology):
        raise InvalidProposalError(violations)
    return Proposal(ontology, tuple(skipped))


def _table(
    source: str, schemas: set[str], key: _TableKey, claims: list[Claim], table_ids: dict[_TableKey, str],
    inferred: Sequence[joins.Chosen] = (),
) -> tuple[Table, list[Skipped]]:
    schema, name = key
    declared = _one(claims, ClaimPredicate.declared_as)
    if declared is None:
        raise AssertionError(f"propose で確かめたはずの declared_as が無い: {key}")
    primary = _one(claims, ClaimPredicate.identified_by)
    if primary is None and len(in_data := _all(claims, ClaimPredicate.unique_in_data)) == 1:
        # 主キーの宣言が無く、データで一意な極小の組が 1 つだけ（T2.7）。2 つ以上なら選ばない（mandara.modeler.keys が問う）
        primary = in_data[0]
    references = _all(claims, ClaimPredicate.references)
    not_null = {_column(c): c for c in _all(claims, ClaimPredicate.not_null)}

    # 代替キー：NULL を持たない列だけでできている一意（仕様 4.2）。主キーと同じ組、同じ組の重複は除く
    seen = [frozenset(_columns(primary))] if primary is not None else []
    alternates: list[Claim] = []
    for unique in _all(claims, ClaimPredicate.unique):
        columns = frozenset(_columns(unique))
        if columns <= not_null.keys() and columns not in seen:
            seen.append(columns)
            alternates.append(unique)
    identity = None
    if primary is not None:
        identity = Identity(
            primary=_columns(primary),
            alternates=[KeyColumns(columns=_columns(c)) for c in alternates] or None,
        )
    nulls_evidence = sorted({not_null[col].id for c in alternates for col in _columns(c)})

    # 宣言された外部キーと同じ結合の推定は、関係にしない（宣言が根拠として強い）
    declared_joins = {joins.claim_join(fk) for fk in references}
    inferred = [c for c in inferred if joins.claim_join(c.claim) not in declared_joins]
    kind, kind_evidence = _kind(declared, primary, [*references, *(c.claim for c in inferred)])
    parts = TableConfidence(entity_group=ENTITY_GROUP_CONFIDENCE, kind=KIND_CONFIDENCE if kind is not None else None)
    evidence = [declared.id, *(c.id for c in [primary, *alternates] if c is not None), *nulls_evidence, *kind_evidence]

    # 中身が同じ外部キー（名前だけ違う冗長な制約）は、1つの関係にまとめて根拠を併記する
    by_join: dict[tuple[_TableKey, tuple[tuple[str, str], ...]], list[Claim]] = {}
    skipped: list[Skipped] = []
    for fk in sorted(references, key=lambda c: c.source.locator):
        target = _present(fk.target, fk)
        target_key = (target.schema_name, target.table)
        if target_key not in table_ids:
            if target.schema_name in schemas:
                raise InvalidInputError(f"範囲内の参照先 {qualify(*target_key)} の表が台帳に無い（台帳が不完全）: {fk.id}")
            skipped.append(Skipped(fk.id, f"参照先 {qualify(*target_key)} が範囲外"))
            continue
        pairs = tuple(sorted(zip(_columns(fk), _present(fk.target_columns, fk), strict=True)))
        by_join.setdefault((target_key, pairs), []).append(fk)
    relations = [
        Relation(
            id="rel_" + _stable_hash(source, schema, name, fks[0].source.locator),
            type=RelationType.has,
            has_kind=HasKind.reference,
            target=SELF if target_key == key else table_ids[target_key],
            via=[ColumnPair.model_validate({"from": a, "to": b})
                 for a, b in zip(_columns(fks[0]), _present(fks[0].target_columns, fks[0]), strict=True)],
            cardinality=Cardinality("n:1"),
            **_proposed(REFERENCE_CONFIDENCE, [fk.id for fk in fks]),
        )
        for (target_key, _), fks in by_join.items()
    ]
    for chosen in inferred:
        c = chosen.claim
        target = _present(c.target, c)
        target_key = (target.schema_name, target.table)
        if target_key not in table_ids:
            raise InvalidInputError(f"推定した参照の参照先 {qualify(*target_key)} の表が台帳に無い（台帳が不完全）: {c.id}")
        relations.append(inferred_relation(c, SELF if target_key == key else table_ids[target_key], chosen.one_to_one,
                                           chosen.confidence, joins.MODELER))

    typed = sorted(_all(claims, ClaimPredicate.typed_as), key=lambda c: _present(c.position, c))
    encoded_claims = _all(claims, ClaimPredicate.encoded_as)
    if twice := sorted(n for n, k in Counter(_column(c) for c in encoded_claims).items() if k > 1):
        raise InvalidInputError(f"{qualify(schema, name)} の同じ列に encoded_as が2つ以上ある（台帳が不正）: {twice}")
    encoded = {_column(c): c for c in encoded_claims}
    if stray := sorted(encoded.keys() - {_column(c) for c in typed}):
        raise InvalidInputError(f"{qualify(schema, name)} の型の主張が無い列に encoded_as がある（台帳が不完全）: {stray}")
    columns = {_column(c): _column_of(c, encoded.get(_column(c))) for c in typed}

    table = Table(
        id=table_ids[key],
        name=qualify(schema, name),
        source=source,
        entity_group=schema,
        kind=kind,
        derived=True if _derived(declared) else None,
        identity=identity,
        columns=columns or None,
        relations=relations or None,
        **_proposed(overall(parts), evidence),
        confidence_parts=parts,
        note="entity_group はスキーマ名からの推定" + ("" if kind is not None else "。kind は未決（規則では決まらない）"),
    )
    return table, skipped


def inferred_relation(claim: Claim, target: str, one_to_one: bool, confidence: float, inferred_by: str) -> Relation:
    """値の包含（included_in）から推定した参照の関係（規則が選んだものも、判断モデルが選んだものも同じ形）。
    target は参照先の表の ID（自分なら SELF）。ID は測定の場所から作る（数が変わっても関係の ID は変わらない）。"""
    if claim.predicate != ClaimPredicate.included_in.value:
        raise ValueError(f"推定した参照の根拠は included_in の主張: {claim.id}（{claim.predicate}）")
    s = claim.subject
    return Relation(
        id="rel_" + _stable_hash(s.source, s.schema_name, s.table, claim.source.locator),
        type=RelationType.has,
        has_kind=HasKind.reference,
        target=target,
        via=[ColumnPair.model_validate({"from": a, "to": b})
             for a, b in zip(_columns(claim), _present(claim.target_columns, claim), strict=True)],
        cardinality=Cardinality("1:1" if one_to_one else "n:1"),
        status=ReviewStatus.proposed, confidence=confidence, evidence=[claim.id], inferred_by=inferred_by,
    )


def _column_of(typed: Claim, encoded: Claim | None) -> Column:
    if encoded is None:
        return Column(name=_column(typed), **_proposed(DECLARED_CONFIDENCE, [typed.id]))
    return Column(name=_column(typed), encoding=_present(encoded.encoding, encoded),
                  **_proposed(min(DECLARED_CONFIDENCE, ENCODING_CONFIDENCE), [typed.id, encoded.id]))


def _kind(declared: Claim, primary: Claim | None, references: list[Claim]) -> tuple[TableKind | None, list[str]]:
    """仕様 4.1 の定義にそのまま当たる表だけに種類を付ける。当たらなければ未決。

    ビュー・マテリアライズドビューは計算で導出された表（derived。kind とは別の軸、_derived）で、種類は規則では
    決めない（未決）。relation の規則は実体の表の識別制約を前提にしており、ビューの主キーは制約として強制されない
    - relation：主キーが、列の組の異なる2本以上の外部キーの和になっている（もの同士の組で識別される中間表。属性はあってよい）。
      各外部キーの列は主キーの真部分集合（1本で主キー全体を覆う外部キーは、1:1 の拡張や is-a であって中間表ではない）
    """
    if _derived(declared) or primary is None:
        return None, []
    key = frozenset(_columns(primary))
    parts: dict[frozenset[str], Claim] = {}
    for fk in references:
        columns = frozenset(_columns(fk))
        if columns < key:
            parts.setdefault(columns, fk)
    if len(parts) >= 2 and frozenset[str]().union(*parts) == key:
        return TableKind.relation, sorted(fk.id for fk in parts.values())
    return None, []


def _derived(declared: Claim) -> bool:
    """ビュー・マテリアライズドビューは計算で導出された表。根拠は declared_as の主張（表の根拠に入っている）。"""
    return declared.object_kind in (ObjectKind.view.value, ObjectKind.materialized_view.value)


class _Annotation(TypedDict):
    status: ReviewStatus
    confidence: float
    evidence: list[str]
    inferred_by: str


def _proposed(confidence: float, evidence: list[str]) -> _Annotation:
    return {"status": ReviewStatus.proposed, "confidence": confidence, "evidence": evidence, "inferred_by": MODELER}


def _stable_hash(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _present[T](value: T | None, claim: Claim) -> T:
    """主張の形の検証で保証された値を取り出す。無ければモデラーのバグ。"""
    if value is None:
        raise AssertionError(f"検証済みの主張に値が無い: {claim.id}")
    return value


def _columns(claim: Claim) -> list[str]:
    return _present(claim.columns, claim)


def _column(claim: Claim) -> str:
    return _present(claim.subject.column, claim)


def _one(claims: list[Claim], predicate: ClaimPredicate) -> Claim | None:
    found = _all(claims, predicate)
    if len(found) > 1:
        raise InvalidInputError(f"{predicate.value} の主張が複数ある: {[c.id for c in found]}")
    return found[0] if found else None


def _all(claims: list[Claim], predicate: ClaimPredicate) -> list[Claim]:
    """主語が表か列かは、述語の形（mandara.om.claims.SHAPES）で決まっている。"""
    return [c for c in claims if c.predicate == predicate.value]
