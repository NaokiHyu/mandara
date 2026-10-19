"""生成パイプライン（仕様 6章）のユースケース：抽出 → 提案 → 評価、と OM の検証。

Application 層。Domain の純粋な関数（抽出器、モデラー、評価、不変条件）と Adapter（コネクタ、ストア）を順に呼ぶ。
コネクタは外から渡す（どの DB かは知らない）。時刻も外から渡す（実行の記録のため。UTC に直して書く）。

作業領域 work/<source>/ の中身（git に乗せない）：
  claims/           主張台帳と、それを作ったときの範囲（scope.json）。同じ置き換えで書くので食い違わない
  ontology/         提案した OM
  eval/report.json  正解 OM との突き合わせ
  runs/<時刻>.json   実行の記録。開始時に running で書き、終わったら succeeded / failed（失敗した段と理由）にする。
                    途中で失敗したとき、どの成果物が今回のものかを記録から判断できるように、段ごとの完了も書く
"""

import hashlib
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from mandara.codecs.detect import Detection, detect
from mandara.codecs.registry import BY_ENCODING, CodecError
from mandara.evaluation import joins as joins_evaluation
from mandara.evaluation.joins import JoinScore
from mandara.evaluation import keys as keys_evaluation
from mandara.evaluation import resemblance as resemblance_evaluation
from mandara.evaluation.score import Report, score
from mandara.extractors import catalog as catalog_extractor
from mandara.extractors import declarations as declaration_extractor
from mandara.extractors import joins as joins_extractor
from mandara.extractors import keys as keys_extractor
from mandara.extractors import values as values_extractor
from mandara.llm.decision import BudgetedDecisionModel, DecisionModel, DecisionResponse
from mandara.llm.model import (
    Budget,
    BudgetedModel,
    BudgetExceededError,
    EstimateViolatedError,
    LanguageModel,
    LlmError,
    StructuredRequest,
    summarize,
)
from mandara.modeler import joins as joins_modeler
from mandara.modeler import keys as keys_modeler
from mandara.modeler import classify
from mandara.modeler import llm as llm_modeler
from mandara.modeler import meaning
from mandara.modeler import nameless_joins
from mandara.modeler import same_meaning
from mandara.modeler import rules as modeler
from mandara.om.invariants import Violation, check
from mandara.om.encoding_traits import ENCODING_TARGETS
from mandara.om.model import (Claim, ClaimPredicate, ReviewStatus, SemanticType, SourceRef, Table, TypeFamily, Unit,
                              ValueType)
from mandara.om.names import qualify, split_qualified
from mandara.om.ontology import Ontology
from mandara.omstore.declaration_store import load_declarations
from mandara.omstore.claim_store import load_claim_ledger, load_ledger_scope, write_claim_ledger
from mandara.omstore.json_store import write_json
from mandara.modeler.rules import InvalidInputError
from mandara.omstore.yaml_store import (
    OntologyWriteRefusedError,
    dump_table,
    dump_vocabulary,
    load_ontology,
    write_ontology,
    write_reviewed_ontology,
)
from mandara.review.decisions import (
    Approve,
    Decision,
    Question,
    ReviewError,
    apply,
    is_approve_all_member,
    is_bulk_candidate,
    is_table_batch_member,
    questions,
)
from mandara.source.catalog import Catalog, CatalogReader, Scope, SourceError
from mandara.source.catalog import Column as CatalogColumn
from mandara.source.catalog import Table as CatalogTable
from mandara.source.coverage import CoverageReader
from mandara.source.inclusion import ColumnProfile, InclusionReader, KeyRef
from mandara.source.rows import ColumnReader
from mandara.source.samples import ValueSampler
from mandara.source.uniqueness import UniquenessReader
from mandara.app.resemblance import ResemblanceMeasured, measure_resemblance
from mandara.extractors import resemblance as resemblance_extractor
from mandara.modeler import resemblance as resemblance_modeler
from mandara.source.values import GeoPoint

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class Workspace:
    root: Path
    """work/<source>。"""
    source: str

    @property
    def claims(self) -> Path:
        return self.root / "claims"

    @property
    def ontology(self) -> Path:
        return self.root / "ontology"

    @property
    def eval_report(self) -> Path:
        return self.root / "eval" / "report.json"

    @property
    def runs(self) -> Path:
        return self.root / "runs"


@dataclass(frozen=True)
class Examined:
    """値を読んで物理的な表現を判別した列。"""

    table: str
    column: str
    detection: Detection
    truncated: bool
    """見本の上限で打ち切った（表にはまだ行がある）。"""


@dataclass(frozen=True)
class Covered:
    """被覆を数えた、主キーを共有する外部キー（is-a の候補）。"""

    table: str
    target: str
    rows: int
    covered: int
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Extracted:
    claims: tuple[Claim, ...]
    tables: int
    scope: Scope
    examined: tuple[Examined, ...]
    not_examined: tuple[values_extractor.NotExamined, ...]
    covered: tuple[Covered, ...]
    keys: "KeysMeasured"
    joins: "JoinsMeasured"
    resemblance: ResemblanceMeasured | None
    """値の似かたの測定（T2.6）。None は測らなかった（呼ぶ側が見本の読み手を渡さなかった。実行の記録にもそう書く）。"""


@dataclass(frozen=True)
class KeysMeasured:
    """キーの測定（T2.7）：主キーの宣言の無い表で、データで一意な極小の列の組を探した数。"""

    tables: int
    """探した表の数（主キーの宣言の無い表。行の無い表と、キーになりうる列の無い表を含む）。"""
    empty: int
    """行が無く、組を探さなかった表の数。"""
    no_columns: int
    """キーになりうる型族の列が無く、組を探さなかった表の数。"""
    uniques: int
    """見つけた組の数（= unique_in_data の主張の数）。"""
    verified: int
    """見本で一意で、DB で数えて確かめた組の数（一意でなかったものを含む）。"""
    ambiguous: tuple[str, ...]
    """一意な組が 2 つ以上あり、規則では主キーを選ばない表（判断モデルに問う。LLM なしの実行では主キーが無いまま）。"""


@dataclass(frozen=True)
class JoinsMeasured:
    """宣言の無い参照の測定（T2.2）：候補の組の数を、足切りの段ごとに。足切りで落とした組は主張にしないので、ここに残す。"""

    columns: int
    """参照元の候補の列の数（型の合うキーが1つ以上ある列）。"""
    typed: int
    """型の合う組の数。"""
    narrowed: int
    """値の数と範囲で残った組の数。"""
    overlapping: int
    """見本の重なりで残り、全部数えた組の数（= included_in の主張の数）。"""
    included: int
    """そのうち包含が 100% の組の数。"""
    chosen: int
    """参照とみなした組の数（mandara.modeler.joins。宣言された外部キーと同じ結合を含む）。"""


@dataclass(frozen=True)
class Proposed:
    proposal: modeler.Proposal
    ledger_hash: str
    """提案の入力（主張台帳）の指紋。主張 ID の集合から決まる。主張 ID は内容から決まり、確信度と抽出器の版は
    含まない（モデラーはどちらも読まない）ので、提案の入力の指紋として足りる。"""


def extract(workspace: Workspace, reader: CatalogReader, scope: Scope, values: ColumnReader,
            coverage: CoverageReader, inclusion: InclusionReader, uniqueness: UniquenessReader,
            sampler: ValueSampler | None) -> Extracted:
    """カタログを読み、実データの主張（主キーの宣言の無い表のデータで一意な組、バイト列の物理的な表現、値の包含、
    主キーを共有する参照の被覆、値の似かた）を足して、
    主張台帳を作り直す。主キーを共有する参照は、宣言された外部キーと、値の包含から参照とみなしたもの（mandara.modeler.joins）
    の両方。値の似かた（T2.6）は sampler があるときだけ測る（None は「測らない」と明示する。測った結果の代わりにはしない）。"""
    catalog = reader.read(scope)
    claims = catalog_extractor.extract(workspace.source, catalog)
    claims += declaration_extractor.extract(workspace.source, catalog, load_declarations(workspace.root))
    keys = measure_keys(workspace.source, catalog, uniqueness)
    claims += keys.claims
    targets, not_examined = values_extractor.targets(catalog, keys.claims)
    examined: list[Examined] = []
    for target in targets:
        sample = values.read_column(target.table.schema, target.table.name, target.key,
                                    target.column.name, values_extractor.SAMPLE_LIMIT)
        detection = detect([row.value for row in sample.rows])
        examined.append(Examined(target.table.qualified_name, target.column.name, detection, sample.truncated))
        if (claim := values_extractor.encoding_claim(workspace.source, target, detection)) is not None:
            claims.append(claim)
    joins = measure_joins(workspace.source, catalog, inclusion, keys.claims)
    claims += joins.claims
    # 主キーを共有する参照の被覆：宣言された外部キーと、事実だけで決めた候補（選び方に依らない。joins.shared_key_candidates）
    declared = {joins_modeler.join_of(t.schema, t.name, fk.columns, fk.target_schema, fk.target_table, fk.target_columns)
                for t in catalog.tables for fk in t.foreign_keys}
    inferred = [(c.subject.schema_name, c.subject.table, values_extractor.InferredReference(
        c.source.locator, tuple(c.columns or []), _ref(c).schema_name, _ref(c).table, tuple(c.target_columns or [])))
                for c in joins_modeler.shared_key_candidates(joins.claims) if joins_modeler.claim_join(c) not in declared]
    covered: list[Covered] = []
    for shared in values_extractor.shared_keys(catalog, inferred):
        fk = shared.foreign_key
        counted = coverage.coverage(shared.table.schema, shared.table.name, fk.columns, shared.target.schema,
                                    shared.target.name, fk.target_columns, shared.split_by,
                                    values_extractor.MAX_DISCRIMINATOR_VALUES)
        found = values_extractor.coverage_claims(workspace.source, shared, counted)
        covered.append(Covered(shared.table.qualified_name, shared.target.qualified_name, counted.rows, counted.covered,
                               tuple(found)))
        claims.extend(found)
    resembled = measure_resemblance(workspace.source, catalog, sampler) if sampler is not None else None
    if resembled is not None:
        claims += resembled.claims
    write_claim_ledger(workspace.claims, workspace.source, catalog.scope.schemas, claims)
    return Extracted(tuple(claims), len(catalog.tables), catalog.scope, tuple(examined), tuple(not_examined),
                     tuple(covered), keys.measured, replace(joins.measured, chosen=len(joins_modeler.choose(claims))),
                     resembled.measured if resembled is not None else None)


@dataclass(frozen=True)
class MeasuredKeys:
    claims: list[Claim]
    measured: KeysMeasured


def measure_keys(source: str, catalog: Catalog, uniqueness: UniquenessReader) -> MeasuredKeys:
    """主キーの宣言の無い表の、データで一意な極小の列の組を探す（mandara.extractors.keys）。"""
    claims: list[Claim] = []
    found = keys_extractor.targets(catalog)
    empty = no_columns = verified = 0
    ambiguous: list[str] = []
    for table in found:
        names = keys_extractor.columns(table)
        if not names:
            no_columns += 1
            continue
        sample = uniqueness.rows(table.schema, table.name, names, keys_extractor.SAMPLE_ROWS)
        empty += sample.rows == 0

        def verify(columns: tuple[str, ...], table: CatalogTable = table, rows: int = sample.rows) -> bool:
            nonlocal verified
            verified += 1
            counted = uniqueness.distinct(table.schema, table.name, columns)
            if counted.rows != rows:
                raise SourceError(f"{table.qualified_name} の行の数が読みの間に変わった（{rows} → {counted.rows}）")
            return counted.distinct == counted.rows

        uniques = keys_extractor.minimal_uniques(names, sample, verify)
        if len(uniques) > 1:
            ambiguous.append(table.qualified_name)
        claims += keys_extractor.claims(source, keys_extractor.Found(table, sample.rows, tuple(uniques)))
    return MeasuredKeys(claims, KeysMeasured(len(found), empty, no_columns, len(claims), verified, tuple(ambiguous)))


@dataclass(frozen=True)
class MeasuredJoins:
    claims: list[Claim]
    measured: JoinsMeasured


def _ref(claim: Claim) -> SourceRef:
    if claim.target is None:
        raise AssertionError(f"included_in に target が無い（主張の検証を通ったはず）: {claim.id}")
    return claim.target


def measure_joins(source: str, catalog: Catalog, inclusion: InclusionReader, observed: Sequence[Claim]) -> MeasuredJoins:
    """値の包含を測る（mandara.extractors.joins の段の順に足切り）。列の様子は1列1回だけ読む。observed は
    unique_in_data の主張（主キーの宣言の無い表のキーの候補）。"""
    profiles: dict[KeyRef, ColumnProfile] = {}

    def profile(table: CatalogTable, column: CatalogColumn) -> ColumnProfile:
        ref = KeyRef(table.schema, table.name, column.name)
        if ref not in profiles:
            profiles[ref] = inclusion.profile(table.schema, table.name, column.name,
                                              column.family in joins_extractor.ORDERED_FAMILIES)
        return profiles[ref]

    found = joins_extractor.keys(catalog, observed)
    for key in found:
        profile(key.table, key.column)
    candidates = joins_extractor.candidates(catalog, found)
    claims: list[Claim] = []
    narrowed = overlapping = 0
    for candidate in candidates:
        kept = joins_extractor.narrow(candidate, profile(candidate.table, candidate.column), profiles)
        narrowed += len(kept)
        if not kept:
            continue
        table, column = candidate.table, candidate.column
        probed = inclusion.probe(table.schema, table.name, column.name, [k.ref for k in kept], joins_extractor.PROBE_SAMPLE)
        overlap = [k for k, n in zip(kept, probed, strict=True) if n > 0]
        overlapping += len(overlap)
        if not overlap:
            continue
        counted = inclusion.inclusion(table.schema, table.name, column.name, [k.ref for k in overlap],
                                      column.family == TypeFamily.integer)
        claims += joins_extractor.claims(source, candidate, overlap, counted)
    included = sum(1 for c in claims if c.distinct_values and c.matched_values == c.distinct_values)
    return MeasuredJoins(claims, JoinsMeasured(len(candidates), sum(len(c.keys) for c in candidates), narrowed, overlapping,
                                        included, 0))


def propose(workspace: Workspace) -> Proposed:
    """主張台帳から OM を提案して書く。範囲は台帳を作ったときのもの。"""
    claims = load_claim_ledger(workspace.claims, workspace.source)
    proposal = modeler.propose(workspace.source, load_ledger_scope(workspace.claims), claims)
    write_ontology(workspace.ontology, proposal.ontology)
    return Proposed(proposal, _ledger_hash(claims))


def evaluate(workspace: Workspace, gold: Path) -> Report:
    """提案した OM を正解 OM と突き合わせ、結果を書く。"""
    gold_ontology = load_ontology(gold)
    report = score(load_ontology(workspace.ontology), gold_ontology)
    write_json(workspace.eval_report, {"gold": _gold_record(gold, gold_ontology), **report_to_json(report)})
    return report


def evaluate_joins(workspace: Workspace, truth: CatalogReader, scope: Scope) -> JoinScore:
    """提案した OM の結合を、元の DB で宣言されていた外部キーと突き合わせる（T2.2。範囲は scope の表だけ）。"""
    ontology = load_ontology(workspace.ontology)
    schemas = set(scope.schemas)
    found = {j for j in joins_evaluation.proposed(ontology) if split_qualified(j[0])[0] in schemas
             and split_qualified(j[2])[0] in schemas}
    return joins_evaluation.score_joins(found, joins_evaluation.truth(truth.read(scope)))


def evaluate_keys(workspace: Workspace, truth: CatalogReader, scope: Scope) -> keys_evaluation.KeyScore:
    """提案した OM の主キーを、元の DB で宣言されていた主キーと突き合わせる（T2.7。範囲は scope の表だけ）。"""
    return keys_evaluation.score(load_ontology(workspace.ontology), truth.read(scope))


def evaluate_resemblance(workspace: Workspace, gold: Path) -> resemblance_evaluation.ResemblanceScore:
    """値の似かた（T2.6）を、正解 OM の同じ意味型の列の組と突き合わせる。"""
    return resemblance_evaluation.score(load_claim_ledger(workspace.claims, workspace.source), load_ontology(gold))


def read_ontology(root: Path) -> Ontology:
    """見るための OM の読み込み（UI・API から）。"""
    return load_ontology(root)


# ---------------------------------------------------------------- 地図の点（ゼロコピーの読み）


class PointsError(Exception):
    """点を読めない（表や列が無い、geo.point の列ではない、物理的な表現が分からない、主キーが無い）。"""


@dataclass(frozen=True)
class Points:
    table: str
    column: str
    points: tuple[tuple[tuple[str, ...], GeoPoint], ...]
    """（行の主キーを文字列にしたもの, 点）。"""
    nulls: int
    failed: tuple[tuple[tuple[str, ...], str], ...]
    """変換できなかった行（行の主キー, 理由）。黙って捨てない。"""
    rows_read: int
    """読んだ行の数 = 点の数 + nulls + failed の数。"""
    limit: int
    truncated: bool


def read_points(ontology: Ontology, table_id: str, column: str, reader: ColumnReader, limit: int) -> Points:
    """OM の geo.point の列の値をソースから読み、点にする。変換は列の encoding のコーデック（仕様 4.2）。"""
    table = ontology.table(table_id)
    if table is None:
        raise PointsError(f"表が無い: {table_id}")
    found = (table.columns or {}).get(column)
    if found is None:
        raise PointsError(f"列が無い: {table.name}.{column}")
    semantic_type = (ontology.vocabulary.semantic_types or {}).get(found.semantic_type or "")
    if semantic_type is None or semantic_type.type != ValueType("geo.point").value:
        raise PointsError(f"{table.name}.{column} は geo.point の列ではない（semantic_type={found.semantic_type}）")
    if found.encoding is None:
        raise PointsError(f"{table.name}.{column} の物理的な表現（encoding）が分からないので、点に変換できない")
    if ENCODING_TARGETS.get(found.encoding) != ValueType("geo.point").value:
        raise PointsError(f"encoding {found.encoding} は geo.point に変換しない（変換先 {ENCODING_TARGETS.get(found.encoding)}）")
    codec = BY_ENCODING.get(found.encoding)
    if codec is None:
        raise PointsError(f"encoding {found.encoding} のコーデックが無い")
    if table.identity is None:
        raise PointsError(f"{table.name} に主キーが無いので、行を特定できない")
    parts = split_qualified(table.name)
    if len(parts) != 2:
        raise PointsError(f"表の名前が「スキーマ.表」ではない: {table.name}")

    sample = reader.read_column(parts[0], parts[1], table.identity.primary, column, limit)
    points: list[tuple[tuple[str, ...], GeoPoint]] = []
    failed: list[tuple[tuple[str, ...], str]] = []
    nulls = 0
    for row in sample.rows:
        key = tuple(str(k) for k in row.key)
        if row.value is None:
            nulls += 1
            continue
        try:
            point = codec.decode(row.value)
        except CodecError as e:
            failed.append((key, str(e)))
            continue
        if not isinstance(point, GeoPoint):
            raise AssertionError(f"geo.point のコーデックが GeoPoint 以外を返した: {type(point).__name__}")
        points.append((key, point))
    return Points(table.id, column, tuple(points), nulls, tuple(failed), len(sample.rows), limit, sample.truncated)


@dataclass(frozen=True)
class Validated:
    ontology: Ontology
    violations: tuple[Violation, ...]


def validate(ontology_root: Path) -> Validated:
    ontology = load_ontology(ontology_root)
    return Validated(ontology, tuple(check(ontology)))


# ---------------------------------------------------------------- LLM による推論（モデラー v1）


@dataclass(frozen=True)
class TableOutcome:
    table: str
    status: str
    """"refined"（1つ以上当たった）/ "nothing_applied"（答えは得たが、当たる項目が無かった）/
    "kept"（当てると検証を通らないので v0 のまま）/ "failed"（答えを得られなかった）/
    "not_asked"（導出された表＝ビュー。問わない。導出を読む T4.1 まで）。"""
    detail: str
    applied: tuple[str, ...]
    dropped: tuple[llm_modeler.Dropped, ...]
    answer: object = None
    """段ごとの答え（判断モデルの選択と確率、LLM の答えそのまま）。同じ問いに同じ答えが返る保証は無いので、再現のために記録する。"""
    propagated: tuple[str, ...] = ()
    """答えの意味型を、外部キーで同じ意味の他の列へ伝播したもの。"""
    not_propagated: tuple[str, ...] = ()
    """伝播しなかった列と理由。"""
    stage_failures: tuple[str, ...] = ()
    """答えを得られなかった段（命名・性質・単位・grain）と理由。その段の項目だけを落として、ほかは当てた。"""


@dataclass(frozen=True)
class Refined:
    order: tuple[str, ...]
    """問うた順（語彙は順に育つので、先の表ほど小さい語彙で答える。評価の偏りの記録）。"""
    outcomes: tuple[TableOutcome, ...]
    stopped: str | None
    """途中で止めた理由（予算、見積もりの崩れ）。None は最後まで回った。"""
    not_reached: tuple[str, ...]
    """止めたために問わなかった表（v0 の提案のまま）。"""
    defect: bool = False
    """バグの疑い（組の食い違い、伝播の後の検証違反）で止めた。それまでの結果は書いてある。実行は失敗にする。"""
    nameless: tuple["NamelessOutcome", ...] = ()
    """名前の手がかりの無い参照を問うた結果（表ごと。表の分類の前に、全部の表について1回）。"""
    keys: tuple["KeyOutcome", ...] = ()
    """主キーの宣言の無い表の主キーを問うた結果（T2.7。名前の手がかりの無い参照の前）。"""
    same_meaning: "SameMeaningRun | None" = None
    """値の似た列の組が同じ意味かを問うた結果（表の分類の後。T2.6）。None は、その段まで来なかった（止めた）。"""


@dataclass(frozen=True)
class SameMeaningTable:
    table: str
    status: str
    """"answered" / "failed"（答えを得られなかった。この表の組は当てない）/ "not_reached"（予算で止めたので問わなかった）/
    "not_applied"（答えは得たが、当てた OM が検証を通らないので当てなかった）。"""
    detail: str
    asked: tuple[str, ...]
    different: tuple[str, ...]
    dropped: tuple[llm_modeler.Dropped, ...]
    answer: object = None


@dataclass(frozen=True)
class SameMeaningRun:
    tables: tuple[SameMeaningTable, ...]
    not_asked: tuple[llm_modeler.Dropped, ...]
    changed: tuple[str, ...]
    """意味型を付けた列（「表.列 意味型」。上書きは元の意味型つき）。"""
    skipped: tuple[llm_modeler.Dropped, ...]
    orphaned: tuple[str, ...]
    """上書きで使う列が無くなった意味型。"""
    violations: tuple[str, ...]
    """当てた結果の検証違反（あれば何も当てていない。バグ）。"""


@dataclass(frozen=True)
class KeyOutcome:
    table: str
    status: str
    """"answered"（答えを得た）/ "failed"（答えを得られなかった。主キーは無いまま）/ "not_reached"（予算で止めたので
    問わなかった）/ "not_applied"（答えは得たが、当てた OM が検証を通らないので当てなかった）。"""
    detail: str
    candidates: tuple[str, ...]
    """選択肢に出した組。"""
    chosen: str | None
    """主キーにした組。None は当てなかった（理由は dropped）。"""
    dropped: tuple[llm_modeler.Dropped, ...]
    answer: object = None


@dataclass(frozen=True)
class NamelessOutcome:
    table: str
    status: str
    """"answered"（答えを得た）/ "failed"（答えを得られなかった。この表の列は推定しない）/ "not_reached"（予算で止めたので
    問わなかった）/ "not_applied"（答えは得たが、足した OM が検証を通らないので当てなかった）。"""
    detail: str
    asked: tuple[str, ...]
    """問うた列。"""
    added: tuple[str, ...]
    """足した関係（「表.列 → 参照先」）。"""
    not_references: tuple[str, ...]
    dropped: tuple[llm_modeler.Dropped, ...]
    answer: object = None


class RefineDefectError(Exception):
    """詰める途中でバグの疑いがあって止めた。それまでの結果（課金済みの答え）は OM と実行の記録に残してある。"""


NAMING_MAX_OUTPUT_TOKENS = 3000
GRAIN_MAX_OUTPUT_TOKENS = 400


class _TableFailed(Exception):
    """この表の問いが答えを得られなかった（記録して次の表へ）。answer はそこまでの答え。"""

    def __init__(self, detail: str, answer: dict[str, object]) -> None:
        super().__init__(detail)
        self.answer = answer


def refine(workspace: Workspace, model: LanguageModel, decider: DecisionModel, first: Sequence[str] = ()) -> Refined:
    """v0 の提案を、表ごとに詰める。分類は判断モデル、名前と文は LLM（T3.7）。first に挙げた表（名前）を先に問う。

    答えを得られなかった表は記録して次へ。予算の上限や見積もりの崩れでは、そこで止めて残りは v0 のまま。
    結果の OM は検証を通したうえで書く（人の判断を含む OM は上書きしない）。
    """
    claims = load_claim_ledger(workspace.claims, workspace.source)
    ontology = load_ontology(workspace.ontology)
    # 問う前に止まる条件を確かめる（予算を使ってから書けない、とならないように）
    if decided := [w for w, a in ontology.annotations() if a.status != ReviewStatus.proposed.value]:
        raise OntologyWriteRefusedError(
            f"人の判断を含む OM は詰め直さない（{len(decided)} 件。例 {decided[:3]}）。再実行での統合は T6.1")
    by_table: dict[str, list[Claim]] = {}
    for claim in claims:
        for name in resemblance_modeler.tables_of(claim):
            by_table.setdefault(name, []).append(claim)
    names = {t.name for t in ontology.tables}
    if unknown := sorted(set(first) - names):
        raise InvalidInputError(f"先に問う表が OM に無い: {unknown}")
    if missing := sorted(names - set(by_table)):
        raise LedgerMismatchError(f"主張台帳に無い表が OM にある（台帳を作り直したら、OM も作り直す）: {missing[:3]}")
    priority = {name: i for i, name in enumerate(first)}
    order = sorted(ontology.tables, key=lambda t: (priority.get(t.name, len(priority)), t.name))
    # 作者の区切り " + "（llm_modeler.AUTHOR_SEPARATOR）は使わない
    inferred_by = f"{llm_modeler.MODELER} ({decider.model}, {model.model})"
    groups = meaning.same_meaning_groups(claims)
    for group in groups.groups:
        meaning.group_semantic_type(ontology, group)  # 入力の OM の組が食い違っていれば、問う前に InconsistentGroupError
    families = meaning.column_families(claims)
    resemblances = resemblance_modeler.index(claims)  # 古い版の信号の主張があれば、問う前に StaleMethodError
    # 主キーの宣言の無い表の主キー（T2.7）：名前の手がかりの無い参照の前に決める（3b は表の 1 列のキーを問わない）
    keyed, keys, stopped = _decide_keys(ontology, claims, decider)
    if violations := check(keyed):
        # 当てていない OM を書くので、記録も「当てなかった」にする（答えは課金済みなので残す）
        keys = [replace(o, status="not_applied", detail="当てた OM が検証を通らない", chosen=None) if o.chosen else o
                for o in keys]
        return _finish(workspace, ontology, order, keys, [], [], f"バグの疑いで止めた: 主キーを当てた OM が検証を"
                       f"通らない: {violations[:3]}", [t.name for t in order], defect=True)
    ontology = keyed
    order = [next(t for t in ontology.tables if t.id == o.id) for o in order]
    if stopped is not None:  # 予算で止めた。それまでに得た答え（課金済み）は当てて書く
        return _finish(workspace, ontology, order, keys, [], [], stopped, [t.name for t in order])
    # 名前の手がかりの無い参照（T2.2 の 3b）：問う前の確かめの後、表の分類の前に決める（決めた参照は、分類と LLM に参照として渡る）
    nameless_by = f"{nameless_joins.MODELER} ({decider.model})"
    decided, nameless, stopped = _decide_nameless(ontology, claims, decider, nameless_by)
    if violations := check(decided):
        # 足していない OM を書くので、記録も「当てなかった」にする（答えは課金済みなので残す）
        nameless = [replace(o, status="not_applied", detail="足した OM が検証を通らない", added=())
                    if o.added else o for o in nameless]
        return _finish(workspace, ontology, order, keys, nameless, [], f"バグの疑いで止めた: 推定した参照を足した OM が検証を"
                       f"通らない: {violations[:3]}", [t.name for t in order], defect=True)
    ontology = decided
    order = [next(t for t in ontology.tables if t.id == o.id) for o in order]
    if stopped is not None:  # 予算で止めた。それまでに得た答え（課金済み）は当てて書く
        return _finish(workspace, ontology, order, keys, nameless, [], stopped, [t.name for t in order])

    outcomes: list[TableOutcome] = []
    stopped: str | None = None
    defects = (meaning.InconsistentGroupError, meaning.PropagationError)
    for i, table in enumerate(order):
        if table.derived:  # ビューは問わない（2026-10-19、ユーザーと合意。元の表の列の写しで、導出は T4.1 で読む）
            outcomes.append(TableOutcome(table.name, "not_asked", "導出された表（ビュー）。導出を読むまで（T4.1）問わない",
                                         (), ()))
            continue
        table_claims = joins_modeler.claims_for(table, by_table.get(table.name, []))
        try:
            hints = llm_modeler.meaning_hints(ontology, table, groups)
        except defects as e:
            return _finish(workspace, ontology, order, keys, nameless, outcomes, f"バグの疑いで止めた: {type(e).__name__}: {e}",
                           [t.name for t in order[i:]], defect=True)
        record: dict[str, object] = {}
        try:
            asked = _ask_table(table, table_claims, ontology, hints, model, decider, record)
        except (BudgetExceededError, EstimateViolatedError) as e:
            stopped = str(e)
            if record:  # 途中まで課金済みの答えは記録に残す（この表には当てない）
                outcomes.append(TableOutcome(table.name, "failed", f"止めた: {e}", (), (), record))
                return _finish(workspace, ontology, order, keys, nameless, outcomes, stopped, [t.name for t in order[i + 1:]])
            return _finish(workspace, ontology, order, keys, nameless, outcomes, stopped, [t.name for t in order[i:]])
        except _TableFailed as e:
            outcomes.append(TableOutcome(table.name, "failed", str(e), (), (), e.answer))
            continue
        if asked.answer is None:
            outcomes.append(TableOutcome(table.name, "nothing_applied", "問うことが無い", (), tuple(asked.notes), record))
            continue
        try:
            applied = llm_modeler.apply_answer(ontology, table.id, asked.answer, table_claims, groups, inferred_by)
            propagated = meaning.propagate(applied.ontology, groups, families)
        except defects as e:
            # この表の答えは当てずに、それまでの OM を書く。答え（課金済み）は記録に残す
            stopped = f"バグの疑いで止めた: {type(e).__name__}: {e}"
            outcomes.append(TableOutcome(table.name, "failed", stopped, (), (), record))
            return _finish(workspace, ontology, order, keys, nameless, outcomes, stopped, [t.name for t in order[i + 1:]],
                           defect=True)
        ontology = propagated.ontology
        status = "kept" if applied.table_kept_as_is else ("refined" if applied.applied else "nothing_applied")
        outcomes.append(TableOutcome(table.name, status, "; ".join(asked.stage_failures), applied.applied,
                                     (*asked.notes, *applied.dropped), record,
                                     tuple(f"{meaning.label(f.member)} {f.semantic_type}" for f in propagated.filled),
                                     tuple(f"{meaning.label(n.member)} {n.semantic_type}: {n.reason}"
                                           for n in propagated.not_filled),
                                     tuple(asked.stage_failures)))
    # 値の似た列の組が同じ意味か（T2.6）：表の分類が全部済んでから、組ごとに問う（分類の問いには混ぜない）
    same_by = f"{same_meaning.MODELER} ({decider.model})"
    ontology, same = _decide_same_meaning(ontology, claims, decider, resemblances, groups, families, same_by)
    if same.violations:  # 当てた OM が検証を通らない：何も当てず（答えは課金済みなので記録に残す）、バグとして止める
        return _finish(workspace, ontology, order, keys, nameless, outcomes, f"バグの疑いで止めた: 同じ意味の答えを当てた OM が"
                       f"検証を通らない: {list(same.violations)[:3]}", [], defect=True, same_meaning_run=same)
    stopped = next((t.detail for t in same.tables if t.status == "not_reached"), None)
    return _finish(workspace, ontology, order, keys, nameless, outcomes, stopped, [], same_meaning_run=same)


def _decide_same_meaning(ontology: Ontology, claims: Sequence[Claim], decider: DecisionModel,
                         resemblances: resemblance_modeler.Resemblances, groups: meaning.MeaningGroups,
                         families: dict[meaning.Member, str], inferred_by: str) -> tuple[Ontology, SameMeaningRun]:
    """値の似た列の組を、表ごとに判断モデルに問い、同じ意味と答えた組の意味型をそろえる（mandara.modeler.same_meaning）。

    答えを得られなかった表は記録して次へ。予算の上限や見積もりの崩れでは、残りの表を not_reached にして、それまでの答えを当てる。"""
    asked = same_meaning.questions(ontology, claims, resemblances, groups, families)
    tables: list[SameMeaningTable] = []
    decided: list[same_meaning.Decided] = []
    stopped: str | None = None
    for t in asked.tables:
        keys = tuple(p.key for p in t.pairs)
        if stopped is not None:
            tables.append(SameMeaningTable(t.table, "not_reached", stopped, keys, (), ()))
            continue
        try:
            response = decider.decide(t.request)
        except (BudgetExceededError, EstimateViolatedError) as e:
            stopped = str(e)
            tables.append(SameMeaningTable(t.table, "not_reached", stopped, keys, (), ()))
            continue
        except LlmError as e:
            tables.append(SameMeaningTable(t.table, "failed", f"{type(e).__name__}: {e}", keys, (), ()))
            continue
        read = same_meaning.read(t, response)
        decided += read.decided
        tables.append(SameMeaningTable(t.table, "answered", "", keys, read.different, read.abstained,
                                       {**_decisions_record(response), "evidence": dict(t.evidence)}))
    applied = same_meaning.apply(ontology, decided, inferred_by)
    if applied.violations:
        tables = [replace(t, status="not_applied", detail="答えを当てた OM が検証を通らない") if t.status == "answered" else t
                  for t in tables]
    return applied.ontology, SameMeaningRun(tuple(tables), asked.not_asked, applied.changed, applied.skipped,
                                            applied.orphaned, applied.violations)


@dataclass
class _Asked:
    """1つの表を問うた結果。"""

    answer: llm_modeler.TableAnswer | None
    """None は問うことが無かった。"""
    notes: list[llm_modeler.Dropped]
    """当てる前に落とした項目（閾値未満、決められない、段の失敗など）と理由。"""
    stage_failures: list[str]
    """答えを得られなかった段（命名・性質・単位・grain）。その段に関わる項目だけを落として続けた。"""


def _ask_table(table: Table, table_claims: joins_modeler.InferenceClaims, ontology: Ontology,
               hints: dict[str, llm_modeler.MeaningHint], model: LanguageModel, decider: DecisionModel,
               record: dict[str, object]) -> _Asked:
    """1つの表を、分類 → 命名 → 性質 → 単位 → grain の順に問い、従来の答えの形に組み立てる（T3.7）。

    答えは段ごとに record に書く（再現のため。答えの形が不正でも、受け取った答えはそのまま残す）。
    分類の失敗は表の失敗（_TableFailed）。命名・性質・単位・grain の失敗は、その段に関わる項目だけを理由つきで落として続け、
    失敗した段を stage_failures に返す（分類の答えは当てる）。予算の例外はそのまま伝える。
    """
    vocabulary = ontology.vocabulary.semantic_types or {}
    notes: list[llm_modeler.Dropped] = []
    failures: list[str] = []

    def failed(stage: str, e: Exception) -> str:
        detail = f"{type(e).__name__}: {e}"
        failures.append(f"{stage}: {detail}")
        return detail

    shared = llm_modeler.shared_key_foreign_keys(table, table_claims, ontology)
    mine = {c.semantic_type for c in (table.columns or {}).values() if c.semantic_type is not None}
    used = {c.semantic_type for t in ontology.tables if t.id != table.id for c in (t.columns or {}).values()
            if c.semantic_type is not None}
    asked = classify.classify(table, table_claims, vocabulary, hints, shared, decider.max_options,
                              f"classify {table.name}", elsewhere_only=used - mine)
    response = None
    if asked.request is not None:
        try:
            response = decider.decide(asked.request)
        except (BudgetExceededError, EstimateViolatedError):
            raise
        except LlmError as e:
            raise _TableFailed(f"分類: {type(e).__name__}: {e}", record) from e
        record["classify"] = _decisions_record(response)
    classified = classify.read_classified(asked, response)
    notes += classified.abstained
    notes += classified.routed
    kind = classified.kind.value if classified.kind is not None else table.kind

    # 命名（LLM）：new になった列
    new_columns = sorted(c for c, p in classified.columns.items() if p.value == classify.NEW)
    named: classify.Named | None = None
    new_types: list[llm_modeler.SemanticTypeAnswer] = []
    if new_columns:
        decided = classify.decided_lines(classified, table_claims, vocabulary)

        def ask_naming(columns: Sequence[str], taken: Collection[str], purpose: str, key: str) -> classify.Named | None:
            """命名を 1 回問う。答えの形の崩れた意味型だけを理由つきで落とす。答えを得られなければ None（理由は記録）。"""
            request = StructuredRequest(
                purpose=purpose, system=classify.NAMING_SYSTEM,
                prompt=classify.naming_prompt(table_claims, taken, kind, hints, columns, decided),
                schema=classify.NamingAnswer.model_json_schema(), max_output_tokens=NAMING_MAX_OUTPUT_TOKENS)
            data: object = None
            try:
                data = model.ask(request).data
                parsed = classify.parse_naming(data)
                record[key] = {"columns": list(columns), "data": data,
                               **({"broken": [{"item": d.item, "reason": d.reason} for d in parsed.broken]}
                                  if parsed.broken else {})}
                read = classify.read_naming(parsed.answer, columns, taken)
                return classify.Named(read.types, (*parsed.broken, *read.dropped))
            except (BudgetExceededError, EstimateViolatedError):
                raise
            except (LlmError, llm_modeler.InvalidAnswerError) as e:
                record[key] = {"columns": list(columns), "data": data,
                               "error": failed("命名" if key == "naming" else "命名の問い直し", e)}
                return None

        named = ask_naming(new_columns, vocabulary, f"name {table.name}", "naming")
        # 答えに無かった列（形の崩れた意味型の列、答えを得られなかったときは全部）だけを、1 回だけ問い直す（2026-10-19、
        # ユーザーと合意。問い直したことは記録の naming_retry に残る）。最初の答えの名前は使えない名前として渡す
        missing = classify.uncovered(named, new_columns) if named is not None else list(new_columns)
        if missing:
            first_names = {t.name for t in named.types} if named is not None else set[str]()
            retry = ask_naming(missing, {*vocabulary, *first_names}, f"name {table.name} (retry)", "naming_retry")
            if named is None and retry is None:
                detail = str(cast(dict[str, object], record["naming_retry"])["error"])
                notes += [llm_modeler.Dropped(f"column {c}", f"命名を得られなかった（問い直しても）: {detail}")
                          for c in new_columns]
            elif named is None:
                assert retry is not None
                named = retry
            elif retry is not None:
                named = classify.merge_named(named, retry, missing)
        if named is not None:
            notes += named.dropped
            props = classify.properties(table, table_claims, named, f"properties {table.name}")
            props_response = None
            if props.request is not None:
                try:
                    props_response = decider.decide(props.request)
                    record["properties"] = _decisions_record(props_response)
                except (BudgetExceededError, EstimateViolatedError):
                    raise
                except LlmError as e:
                    detail = failed("性質", e)
                    record["properties"] = {"error": detail}
                    notes += [llm_modeler.Dropped(f"column {c}", f"意味型 {t.name} の性質を得られなかった: {detail}")
                              for t in named.types for c in t.columns]
                    named = classify.Named((), named.dropped)
            new_types, dropped = classify.read_properties(table_claims, named, props, props_response)
            notes += dropped

    # 単位：単位の要る意味型の列
    final_types = {name: d for name, d in ((n, vocabulary.get(c.semantic_type or "")) for n, c in
                                           (table.columns or {}).items()) if d is not None}
    new_by_name = {t.name: t for t in new_types}
    for name, picked in classified.columns.items():
        if picked.value != classify.NEW:
            final_types[name] = vocabulary[picked.value]
        elif named is not None:
            t = next((t for t in named.types if name in t.columns), None)
            if t is not None and (a := new_by_name.get(t.name)) is not None:
                final_types[name] = SemanticType(name=a.name, type=a.type, additivity=a.additivity,
                                                 requires_unit=a.requires_unit)
    needs_unit = sorted(name for name in classified.columns
                        if name in final_types and final_types[name].requires_unit)
    code_columns = sorted(name for name, d in final_types.items() if d.type == ValueType.code.value)
    money = sorted(name for name in needs_unit if final_types[name].type == ValueType.money.value)
    unit_asked = classify.units(table_claims, needs_unit, money, code_columns, f"units {table.name}")
    unit_answers: dict[str, tuple[Unit | None, str | None, float]] = {}
    if unit_asked.request is not None:
        try:
            unit_response = decider.decide(unit_asked.request)
            record["units"] = _decisions_record(unit_response)
            unit_answers, dropped = classify.read_units(unit_response)
            notes += dropped
        except (BudgetExceededError, EstimateViolatedError):
            raise
        except LlmError as e:
            detail = failed("単位", e)
            record["units"] = {"error": detail}
            notes += [llm_modeler.Dropped(f"column {c}", f"単位を得られなかった: {detail}") for c in needs_unit]

    # grain（LLM）：要る表で、まだ無いとき
    grain: str | None = None
    if not (table.grain or "").strip() and table.status == ReviewStatus.proposed.value and classify.needs_grain(
            kind, final_types):
        request = StructuredRequest(
            purpose=f"grain {table.name}", system=classify.GRAIN_SYSTEM,
            prompt=classify.grain_prompt(table_claims, kind, hints,
                                         classify.decided_lines(classified, table_claims, vocabulary, {
                                             n: d for n, d in final_types.items()  # 単位が決まらず落ちる列は除く
                                             if n not in needs_unit or n in unit_answers})),
            schema=classify.GrainAnswer.model_json_schema(), max_output_tokens=GRAIN_MAX_OUTPUT_TOKENS)
        grain_data: object = None
        try:
            grain_data = model.ask(request).data
            record["grain"] = {"data": grain_data}
            grain = classify.parse_grain(grain_data).grain
        except (BudgetExceededError, EstimateViolatedError):
            raise
        except (LlmError, llm_modeler.InvalidAnswerError) as e:
            detail = failed("grain", e)
            record["grain"] = {"data": grain_data, "error": detail}
            notes.append(llm_modeler.Dropped("grain", f"grain を得られなかった: {detail}"))

    if not record:  # 何も問わなかった
        return _Asked(None, notes, failures)
    answer, dropped = classify.assemble(table, table_claims, asked, classified, new_types, named, unit_answers,
                                        unit_asked.evidence, grain)
    return _Asked(answer, notes + dropped, failures)


def _decisions_record(response: DecisionResponse) -> dict[str, object]:
    """判断の答えの記録：選んだもの、0 でない確率、答えたモデルの版。"""
    return {"model": response.model, "request_id": response.request_id, "answers": {
        key: {"choice": a.choice, "probabilities": {o: p for o, p in a.probabilities.items() if p > 0}}
        for key, a in sorted(response.answers.items())}}


def _finish(workspace: Workspace, ontology: Ontology, order: Sequence[Table], keys: Sequence[KeyOutcome],
            nameless: Sequence[NamelessOutcome], outcomes: list[TableOutcome], stopped: str | None, not_reached: list[str],
            defect: bool = False, same_meaning_run: SameMeaningRun | None = None) -> Refined:
    """same_meaning_run が None なのは、同じ意味の段まで来なかったとき（止めた）。"""
    write_ontology(workspace.ontology, ontology)
    return Refined(tuple(t.name for t in order), tuple(outcomes), stopped, tuple(not_reached), defect, tuple(nameless),
                   keys=tuple(keys), same_meaning=same_meaning_run)


def _decide_keys(ontology: Ontology, claims: Sequence[Claim],
                 decider: DecisionModel) -> tuple[Ontology, list[KeyOutcome], str | None]:
    """主キーの宣言の無い表の主キーを、表ごとに判断モデルに問い、選んだ組を主キーにする（mandara.modeler.keys）。

    答えを得られなかった表は記録して次へ（主キーは無いまま）。予算の上限や見積もりの崩れでは、そこで止めて理由を返す
    （それまでの答えは当てる）。"""
    if keys_modeler.MAX_CANDIDATES + 2 > decider.max_options:
        raise ValueError(f"主キーの選択肢 {keys_modeler.MAX_CANDIDATES + 2} が判断モデルの上限 {decider.max_options} を超える")
    outcomes: list[KeyOutcome] = []
    decided: list[keys_modeler.Decided] = []
    stopped: str | None = None
    for question in keys_modeler.questions(ontology, claims):
        name, options = question.table.name, tuple(c.option for c in question.candidates)
        if stopped is not None:
            outcomes.append(KeyOutcome(name, "not_reached", stopped, options, None, ()))
            continue
        try:
            response = decider.decide(question.request)
        except (BudgetExceededError, EstimateViolatedError) as e:
            stopped = str(e)
            outcomes.append(KeyOutcome(name, "not_reached", stopped, options, None, ()))
            continue
        except LlmError as e:
            outcomes.append(KeyOutcome(name, "failed", f"{type(e).__name__}: {e}", options, None, ()))
            continue
        read = keys_modeler.read(question, response)
        answer = {**_decisions_record(response), "evidence": keys_modeler.evidence(question)}
        if isinstance(read, keys_modeler.Decided):
            decided.append(read)
            outcomes.append(KeyOutcome(name, "answered", "", options, read.candidate.option, (), answer))
        else:
            outcomes.append(KeyOutcome(name, "answered", "", options, None, (read,), answer))
    return keys_modeler.apply(ontology, decided), outcomes, stopped


def _decide_nameless(ontology: Ontology, claims: Sequence[Claim], decider: DecisionModel,
                     inferred_by: str) -> tuple[Ontology, list[NamelessOutcome], str | None]:
    """名前の手がかりの無い包含を、表ごとに判断モデルに問い、選んだ参照を OM に足す（mandara.modeler.nameless_joins）。

    答えを得られなかった表は記録して次へ（その表の列は推定しない）。予算の上限や見積もりの崩れでは、そこで止めて理由を返す
    （それまでの答えは当てる）。選択肢が判断モデルの上限を超える問いは作らない（MAX_TARGETS は上限より小さい）。"""
    if nameless_joins.MAX_TARGETS + 2 > decider.max_options:
        raise ValueError(f"名前の無い参照の選択肢 {nameless_joins.MAX_TARGETS + 2} が判断モデルの上限 "
                         f"{decider.max_options} を超える")
    outcomes: list[NamelessOutcome] = []
    decided: list[nameless_joins.Decided] = []
    stopped: str | None = None
    for asked in nameless_joins.questions(ontology, claims):
        columns = tuple(a.column for a in asked.asked)
        name = asked.table.name
        if stopped is not None:
            outcomes.append(NamelessOutcome(name, "not_reached", stopped, columns, (), (), ()))
            continue
        try:
            response = decider.decide(asked.request)
        except (BudgetExceededError, EstimateViolatedError) as e:
            stopped = str(e)
            outcomes.append(NamelessOutcome(name, "not_reached", stopped, columns, (), (), ()))
            continue
        except LlmError as e:
            outcomes.append(NamelessOutcome(name, "failed", f"{type(e).__name__}: {e}", columns, (), (), ()))
            continue
        read = nameless_joins.read(asked, response)
        decided += read.decided
        outcomes.append(NamelessOutcome(name, "answered", "", columns,
                                        tuple(f"{d.asked.column} → {d.target.option}" for d in read.decided),
                                        read.not_references, read.abstained,
                                        {**_decisions_record(response), "evidence": nameless_joins.evidence(asked)}))
    return nameless_joins.apply(ontology, decided, inferred_by), outcomes, stopped


# ---------------------------------------------------------------- レビュー（仕様 6.4、9.3）


class LedgerMismatchError(Exception):
    """主張台帳と OM が食い違う（OM の根拠の主張が台帳に無い）。台帳を作り直したあとに OM を作り直していない、など。"""


BULK_MINIMUM = 0.95
"""一括承認の閾値の下限（仕様 9.3 の既定）。これより下げてよいかは未決（仕様 15.2）。"""


class BulkThresholdError(Exception):
    """一括承認の閾値が、許す範囲（BULK_MINIMUM 以上 1 以下）の外。"""


@dataclass(frozen=True)
class ReviewQueue:
    questions: tuple[Question, ...]
    evidence: dict[str, str]
    """主張 ID → 人が読める根拠の1行。"""


def review_queue(workspace: Workspace) -> ReviewQueue:
    """まだ判断されていない提案を、問いの列にする。根拠は主張台帳から読める形にして添える。"""
    claims = {c.id: c for c in load_claim_ledger(workspace.claims, workspace.source)}
    queue = tuple(questions(load_ontology(workspace.ontology)))
    wanted = {e for q in queue for e in q.evidence}
    if missing := sorted(wanted - claims.keys()):
        raise LedgerMismatchError(f"提案の根拠の主張が台帳に無い（台帳を作り直したら、OM も作り直す）: {missing[:3]}")
    return ReviewQueue(queue, {cid: _describe(claims[cid]) for cid in wanted})


def answer(workspace: Workspace, location: str, decision: Decision, reviewer: str, on: date) -> None:
    """1つの問いに答え、すぐ書く（途中でやめても、そこまでの判断は残る）。"""
    ontology = load_ontology(workspace.ontology)
    write_reviewed_ontology(workspace.ontology, apply(ontology, location, decision, reviewer, on))


def bulk_candidates(workspace: Workspace, threshold: float) -> list[Question]:
    """一括承認の候補の一覧（仕様 9.3：確信度が閾値以上の提案は一覧で一括承認できる）。種類を選ぶ必要のある表は含めない。

    人は一覧を見てから bulk_approve で確定する。
    """
    if not BULK_MINIMUM <= threshold <= 1.0:
        raise BulkThresholdError(f"一括承認の閾値は {BULK_MINIMUM} 以上 1 以下（指定 {threshold}）。下げてよいかは未決（仕様 15.2）")
    return [q for q in questions(load_ontology(workspace.ontology)) if is_bulk_candidate(q, threshold)]


def bulk_approve(workspace: Workspace, locations: Sequence[str], threshold: float, reviewer: str, on: date) -> int:
    """一覧で見せた候補（locations）を承認する。今も候補のままであることを確かめる（見せた後に変わっていれば止める）。"""
    candidates = {q.location for q in bulk_candidates(workspace, threshold)}
    if stale := [loc for loc in locations if loc not in candidates]:
        raise LedgerMismatchError(f"見せた一覧と今の候補が食い違う（{len(stale)} 件。例 {stale[:3]}）。一覧を出し直す")
    ontology = load_ontology(workspace.ontology)
    for location in locations:
        ontology = apply(ontology, location, Approve(), reviewer, on)
    if locations:
        write_reviewed_ontology(workspace.ontology, ontology)
    return len(locations)


def table_batch(workspace: Workspace, table_id: str) -> list[Question]:
    """表ごとの一括承認の候補（仕様 9.3）。人は一覧を見てから approve_table で確定する。種類を選ぶ問いは1件ずつ答える。"""
    return _table_batch(load_ontology(workspace.ontology), table_id)


def _table_batch(ontology: Ontology, table_id: str) -> list[Question]:
    if ontology.table(table_id) is None:
        raise ReviewError(f"表が無い: {table_id}")
    return [q for q in questions(ontology) if is_table_batch_member(q, table_id)]


def approve_all(workspace: Workspace, locations: Sequence[str], reviewer: str, on: date) -> int:
    """開発用の全承認：見せた問い（locations）が、今の種類を選ばなくてよい未判断の問いとちょうど同じなら、すべて承認する。
    中身を見ずに固定するので、開発中に承認済みの状態を試すためだけに使う（API は serve --dev のときだけ受け付ける）。
    全部を当ててから1回で書く（途中で当てられない問いがあれば、何も書かない）。"""
    ontology = load_ontology(workspace.ontology)
    candidates = [q.location for q in questions(ontology) if is_approve_all_member(q)]
    if not candidates:
        raise ReviewError("承認する問いが無い")
    if sorted(locations) != sorted(candidates):
        raise LedgerMismatchError(f"見せた一覧（{len(locations)} 件）と今の候補（{len(candidates)} 件）が食い違う。一覧を出し直す")
    for location in candidates:
        ontology = apply(ontology, location, Approve(), reviewer, on)
    write_reviewed_ontology(workspace.ontology, ontology)
    return len(candidates)


def approve_table(workspace: Workspace, table_id: str, locations: Sequence[str], reviewer: str, on: date) -> int:
    """一覧で見せた表の問い（locations）を承認する。今もその表の候補のままであることを確かめる（見せた後に変わっていれば止める）。
    候補の確認と承認は同じ読み込みの OM で行い、全部を当ててから1回で書く（途中で当てられない問いがあれば、何も書かない）。"""
    if not locations:
        raise ReviewError("承認する問いが無い")
    ontology = load_ontology(workspace.ontology)
    candidates = {q.location for q in _table_batch(ontology, table_id)}
    if stale := [loc for loc in locations if loc not in candidates]:
        raise LedgerMismatchError(f"見せた一覧と今の候補が食い違う（{len(stale)} 件。例 {stale[:3]}）。一覧を出し直す")
    for location in locations:
        ontology = apply(ontology, location, Approve(), reviewer, on)
    write_reviewed_ontology(workspace.ontology, ontology)
    return len(locations)


def _describe(claim: Claim) -> str:
    if claim.predicate == ClaimPredicate.included_in.value:
        return _inclusion_text(claim)
    detail = f" · {claim.text[:60]}" if claim.text else ""
    return f"{claim.predicate} · {claim.source.locator}{detail}"


def _inclusion_text(claim: Claim) -> str:
    """値の包含の1行：何個の値のうち何個が参照先のキーにあるか、参照先のキーのどれだけを使うか、値の重なり。"""
    target = _ref(claim)
    distinct, matched, keys, repeats = (claim.distinct_values, claim.matched_values, claim.target_values, claim.max_repeats)
    if distinct is None or matched is None or keys is None or repeats is None:
        raise AssertionError(f"included_in に数が無い（主張の検証を通ったはず）: {claim.id}")
    column, key = ", ".join(claim.columns or []), ", ".join(claim.target_columns or [])
    found = "すべて" if matched == distinct else f"{matched:,}"
    used = "（キーの値が無い）" if keys == 0 else f"（キー {keys:,} 値の {matched / keys * 100:.0f}% を使う）"
    shape = "1 値は 1 行（1:1 の形）" if repeats == 1 else f"1 値あたり最大 {repeats:,} 行"
    return (f"値の包含 · {column} の {distinct:,} 値の{found}が {qualify(target.schema_name, target.table)}({key}) に"
            f"ある{used} · {shape}")


@dataclass(frozen=True)
class RunResult:
    extracted: Extracted
    proposed: Proposed
    refined: Refined | None
    report: Report | None
    record: Path


@dataclass(frozen=True)
class Inference:
    """推論に使うモデル。LLM（名前と文）と判断モデル（分類）は1つの予算を共有する。"""

    model: BudgetedModel
    decider: BudgetedDecisionModel

    def __post_init__(self) -> None:
        if self.model.budget is not self.decider.budget:
            raise ValueError("LLM と判断モデルは同じ予算を共有する")

    @property
    def budget(self) -> Budget:
        return self.model.budget


def run(workspace: Workspace, reader: CatalogReader, values: ColumnReader, coverage: CoverageReader,
        inclusion: InclusionReader, uniqueness: UniquenessReader, sampler: ValueSampler | None, scope: Scope,
        gold: Path | None, clock: Clock,
        inference: Inference | None = None) -> RunResult:
    """抽出 → 提案 → （LLM があれば）推論 → （正解があれば）評価を通しで行い、実行の記録を書く。失敗も記録してから伝える。"""
    started = _utc(clock())
    log = _RunLog(workspace.runs / f"{started.strftime('%Y%m%dT%H%M%S%fZ')}.json", clock, {
        "status": "running",
        "started_at": started.isoformat(),
        "source": workspace.source,
        "requested_scope": list(scope.schemas),
        "versions": {"extractor": catalog_extractor.EXTRACTOR, "values_extractor": values_extractor.EXTRACTOR,
                     "declaration_extractor": declaration_extractor.EXTRACTOR, "joins_extractor": joins_extractor.EXTRACTOR,
                     "keys_extractor": keys_extractor.EXTRACTOR,
                     "modeler": modeler.MODELER, "joins_modeler": joins_modeler.MODELER,
                     "nameless_joins_modeler": nameless_joins.MODELER, "keys_modeler": keys_modeler.MODELER,
                     "resemblance_extractor": resemblance_extractor.EXTRACTOR,
                     "same_meaning_modeler": same_meaning.MODELER},
        "completed": [],
    })
    with log.stage("extract"):
        extracted = extract(workspace, reader, scope, values, coverage, inclusion, uniqueness, sampler)
        log.update(scope=list(extracted.scope.schemas), tables=extracted.tables,
                   claims=dict(sorted(Counter(c.predicate for c in extracted.claims).items())),
                   encodings={"examined": [_examined_record(e) for e in extracted.examined],
                              "not_examined": [{"table": n.table, "column": n.column, "reason": n.reason}
                                               for n in extracted.not_examined]},
                   shared_keys=[{"table": c.table, "target": c.target, "rows": c.rows, "covered": c.covered,
                                 "claims": [_coverage_text(x) for x in c.claims]} for c in extracted.covered],
                   keys={**vars(extracted.keys), "ambiguous": list(extracted.keys.ambiguous)},
                   joins=vars(extracted.joins),
                   resemblance=vars(extracted.resemblance) if extracted.resemblance is not None else "not measured")
    with log.stage("propose"):
        proposed = propose(workspace)
        log.update(ledger_hash=proposed.ledger_hash, proposal=_proposal_counts(proposed.proposal.ontology),
                   skipped=[{"claim": s.claim, "reason": s.reason} for s in proposed.proposal.skipped])
    refined = None
    if inference is not None:
        with log.stage("refine"):
            first = [t.name for t in load_ontology(gold).tables] if gold is not None else []
            try:
                refined = refine(workspace, inference.model, inference.decider, first)
            finally:
                spends = inference.budget.spends
                log.update(llm={"model": inference.model.model, "decision_model": inference.decider.model,
                                "limit_usd": inference.budget.limit_usd, **summarize(spends),
                                "by_model": {m: summarize([s for s in spends if s.model == m])
                                             for m in sorted({s.model for s in spends})}})
            log.update(refine={
                "stopped": refined.stopped, "not_reached": list(refined.not_reached),
                "outcomes": dict(sorted(Counter(o.status for o in refined.outcomes).items())),
                "stage_failures": sum(len(o.stage_failures) for o in refined.outcomes),
                "order": list(refined.order),
                "tables": [{"table": o.table, "status": o.status, "detail": o.detail, "applied": list(o.applied),
                            "dropped": [{"item": d.item, "reason": d.reason} for d in o.dropped],
                            "propagated": list(o.propagated), "not_propagated": list(o.not_propagated),
                            "stage_failures": list(o.stage_failures),
                            "answer": o.answer}
                           for o in refined.outcomes],
                "keys": [{"table": o.table, "status": o.status, "detail": o.detail, "candidates": list(o.candidates),
                          "chosen": o.chosen, "dropped": [{"item": d.item, "reason": d.reason} for d in o.dropped],
                          "answer": o.answer} for o in refined.keys],
                "nameless": [{"table": o.table, "status": o.status, "detail": o.detail, "asked": list(o.asked),
                              "added": list(o.added), "not_references": list(o.not_references),
                              "dropped": [{"item": d.item, "reason": d.reason} for d in o.dropped], "answer": o.answer}
                             for o in refined.nameless],
                "same_meaning": _same_meaning_record(refined.same_meaning),
            })
            if refined.defect:
                raise RefineDefectError(refined.stopped)
    report = None
    if gold is not None:
        with log.stage("evaluate"):
            report = evaluate(workspace, gold)
            log.update(gold=_gold_record(gold, load_ontology(gold)), evaluation={
                a.aspect: {"correct": a.correct, "gold": a.gold, "proposed": a.proposed} for a in report.aspects})
    log.update(status="succeeded", finished_at=_utc(clock()).isoformat())
    return RunResult(extracted, proposed, refined, report, log.path)


def _same_meaning_record(run: SameMeaningRun | None) -> object:
    if run is None:
        return "not reached"
    return {"changed": list(run.changed), "orphaned": list(run.orphaned), "violations": list(run.violations),
            "skipped": [{"item": d.item, "reason": d.reason} for d in run.skipped],
            "not_asked": [{"item": d.item, "reason": d.reason} for d in run.not_asked],
            "tables": [{"table": t.table, "status": t.status, "detail": t.detail, "asked": list(t.asked),
                        "different": list(t.different), "dropped": [{"item": d.item, "reason": d.reason} for d in t.dropped],
                        "answer": t.answer} for t in run.tables]}


def _coverage_text(claim: Claim) -> str:
    if claim.predicate == ClaimPredicate.discriminated_by.value:
        return f"discriminated_by {claim.discriminator} in {claim.discriminator_values}"
    return str(claim.predicate)


def _examined_record(e: Examined) -> dict[str, object]:
    d = e.detection
    return {"table": e.table, "column": e.column, "values": d.values, "nulls": d.nulls, "truncated": e.truncated,
            "encoding": d.encoding, "reason": d.reason,
            "tallies": [{"encoding": t.encoding, "matched": t.matched, "decoded": t.decoded} for t in d.tallies]}


class _RunLog:
    """実行の記録。変わるたびに書き直す（途中で落ちても、そこまでが残る）。"""

    def __init__(self, path: Path, clock: Clock, initial: dict[str, Any]) -> None:
        self.path = path
        self._clock = clock
        self._data = dict(initial)
        self._completed: list[str] = []
        write_json(self.path, self._data)

    def update(self, **fields: object) -> None:
        self._data.update(fields)
        write_json(self.path, self._data)

    def stage(self, name: str) -> "_Stage":
        return _Stage(self, name)

    def finish_stage(self, name: str) -> None:
        self._completed.append(name)
        self.update(completed=list(self._completed))

    def fail(self, name: str, error: BaseException) -> None:
        self.update(status="failed", failed_stage=name, error=f"{type(error).__name__}: {error}",
                    finished_at=_utc(self._clock()).isoformat())


class _Stage:
    def __init__(self, log: _RunLog, name: str) -> None:
        self._log = log
        self._name = name

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, _tb: object) -> None:
        """None を返す（例外を握りつぶさずに伝える）。"""
        if error is not None:
            self._log.fail(self._name, error)
        else:
            self._log.finish_stage(self._name)


def report_to_json(report: Report) -> dict[str, object]:
    return {
        "aspects": [
            {"aspect": a.aspect, "gold": a.gold, "proposed": a.proposed, "correct": a.correct,
             "recall": a.recall, "false_rate": a.false_rate, "missed": list(a.missed), "wrong": list(a.wrong)}
            for a in report.aspects
        ],
        "unscored_tables": report.unscored_tables,
        "unscored_relations": report.unscored_relations,
    }


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"時計がタイムゾーンの無い時刻を返した: {moment}")
    return moment.astimezone(UTC)


def _gold_record(path: Path, gold: Ontology) -> dict[str, object]:
    """どの正解と比べたか。パスと、正規形の内容のハッシュ。"""
    canonical = dump_vocabulary(gold.vocabulary) + "".join(dump_table(t) for t in sorted(gold.tables, key=lambda t: t.id))
    return {"path": str(path), "tables": len(gold.tables),
            "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]}


def _ledger_hash(claims: tuple[Claim, ...]) -> str:
    return hashlib.sha256("\n".join(sorted(c.id for c in claims)).encode()).hexdigest()[:16]


def _proposal_counts(ontology: Ontology) -> dict[str, object]:
    return {
        "tables": len(ontology.tables),
        "kinds": dict(sorted(Counter(t.kind or "undecided" for t in ontology.tables).items())),
        "derived": sum(t.derived is True for t in ontology.tables),
        "relations": sum(len(t.relations or []) for t in ontology.tables),
        "columns": sum(len(t.columns or {}) for t in ontology.tables),
    }
