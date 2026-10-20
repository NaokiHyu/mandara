"""主キーの宣言の無い表の主キーを、データで一意な組（unique_in_data）から選ぶ（T2.7）。

Domain 層の純粋な関数（呼び出しは Application がポート経由で行う）。
- 候補が 1 組だけの表は規則（mandara.modeler.rules）が主キーにする。ここで問うのは、identity の無い実体の表で、
  候補が 2 組以上あるもの
- 表ごとに 1 回、判断モデルに問う。選択肢は候補（ほかの表から参照されている数の多い順、列の少ない順、位置の順）を
  MAX_CANDIDATES 組まで ＋「どれでもない」＋「決められない」
- 問いに書く事実は、主張から確かめたものだけ：列の型族と位置、データで一意で NULL が無いこと、ほかの表の列の値が
  すべてその列の値に含まれる数（包含 100% の included_in。1 列の組だけ）
- 確信度は判断モデルの確率。ただし CONFIDENCE_CAP を超えない。閾値（classify.THRESHOLD）未満・「決められない」・
  「どれでもない」は当てない。理由つきで記録する
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mandara.llm.decision import Choice, DecisionRequest, DecisionResponse
from mandara.modeler import classify, joins
from mandara.modeler.llm import Dropped
from mandara.om.confidence import with_part
from mandara.om.model import Claim, ClaimPredicate, Identity, Table
from mandara.om.names import qualify
from mandara.om.ontology import Ontology

MODELER = "modeler-keys 0.1"
"""データで一意な組から、判断モデルが選んだ主キーの提案者。問い方や選び方を変えたら版を上げる。"""
CONFIDENCE_CAP = 0.7
"""確信度の上限（宣言の無い推定。人のレビューに回る）。"""
MAX_CANDIDATES = 5
NONE_OF_THESE = "none_of_these"


@dataclass(frozen=True)
class Candidate:
    claim: Claim
    """unique_in_data の主張。"""
    referenced: int
    """ほかの表の列で、値がすべてこの組（1 列）の値に含まれるもの（包含 100% の included_in）の数。"""

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.claim.columns or [])

    @property
    def option(self) -> str:
        return "key " + ", ".join(self.columns)


@dataclass(frozen=True)
class TableQuestion:
    table: Table
    candidates: tuple[Candidate, ...]
    omitted: int
    request: DecisionRequest


def _referenced(claims: Sequence[Claim]) -> Mapping[tuple[str, str], int]:
    """（表、列）→ ほかの表の列で、値がすべてその列の値に含まれるものの数。"""
    out: dict[tuple[str, str], int] = defaultdict(int)
    for c in claims:
        if c.predicate != ClaimPredicate.included_in.value or not c.distinct_values or c.matched_values != c.distinct_values:
            continue
        t, keys = c.target, c.target_columns or []
        if t is None or len(keys) != 1:
            raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {c.id}")
        if (t.schema_name, t.table) != (c.subject.schema_name, c.subject.table):
            out[(qualify(t.schema_name, t.table), keys[0])] += 1
    return out


def questions(ontology: Ontology, claims: Sequence[Claim]) -> list[TableQuestion]:
    """問う表（表の名前の順）。問うものが無ければ空。"""
    by_table: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_table[qualify(c.subject.schema_name, c.subject.table)].append(c)
    referenced = _referenced(claims)
    out: list[TableQuestion] = []
    for table in sorted(ontology.tables, key=lambda t: t.name):
        table_claims = by_table.get(table.name, [])
        uniques = [c for c in table_claims if c.predicate == ClaimPredicate.unique_in_data.value]
        if table.identity is not None or table.derived or len(uniques) < 2:
            continue
        positions = {c.subject.column: c.position for c in table_claims if c.predicate == ClaimPredicate.typed_as.value}

        def rank(c: Candidate) -> tuple[int, int, list[int]]:
            return (-c.referenced, len(c.columns), [_position(positions, table.name, n) for n in c.columns])

        found = [Candidate(u, referenced.get((table.name, columns[0]), 0) if len(columns := u.columns or []) == 1 else 0)
                 for u in uniques]
        ranked = tuple(sorted(found, key=rank)[:MAX_CANDIDATES])
        material = classify.state(joins.claims_for(table, table_claims))
        out.append(TableQuestion(table, ranked, max(0, len(found) - MAX_CANDIDATES), DecisionRequest(
            f"primary key {table.name}", material, {"primary key": _choice(ranked, max(0, len(found) - MAX_CANDIDATES))})))
    return out


def _position(positions: Mapping[str | None, int | None], table: str, column: str) -> int:
    position = positions.get(column)
    if position is None:
        raise ValueError(f"一意な組の列 {table}.{column} に型の主張（位置）が無い（台帳が不完全）")
    return position


def _choice(candidates: Sequence[Candidate], omitted: int) -> Choice:
    options: dict[str, str | None] = {}
    for c in candidates:
        refs = (f" The values of {c.referenced} columns in other tables are all among its values." if c.referenced
                else "")
        options[c.option] = (f"The columns {', '.join(f'`{n}`' for n in c.columns)} identify a row of the `table`."
                             + refs)
    options[NONE_OF_THESE] = "None of these column sets is what identifies a row; they are only unique by coincidence."
    options[classify.UNCLEAR] = "The facts do not decide it."
    note = f" {omitted} more unique column sets are not listed." if omitted else ""
    return Choice({"facts": ["The `table` declares no primary key. Every listed column set is unique and never NULL "
                             f"in the data (and no smaller part of it is).{note}"],
                   "question": "Which column set is the primary key of the `table`: the columns that identify what a "
                               "row is about, not columns that are unique only by coincidence (timestamps, amounts, "
                               "random identifiers kept for replication)? Judge from the names and comments, the types, "
                               "the column positions, and whether other tables refer to it."},
                  options)


@dataclass(frozen=True)
class Decided:
    question: TableQuestion
    candidate: Candidate
    probability: float

    @property
    def confidence(self) -> float:
        return min(self.probability, CONFIDENCE_CAP)


def read(question: TableQuestion, response: DecisionResponse) -> Decided | Dropped:
    answer = response.answers["primary key"]
    name = question.table.name
    if answer.choice == classify.UNCLEAR:
        return Dropped(name, f"判断モデルが決められないと答えた（確率 {answer.probability:.2f}）")
    if answer.probability < classify.THRESHOLD:
        return Dropped(name, f"確信度 {answer.probability:.2f} が閾値 {classify.THRESHOLD} 未満（{answer.choice}）")
    if answer.choice == NONE_OF_THESE:
        return Dropped(name, f"判断モデルがどの組も主キーではないと答えた（確率 {answer.probability:.2f}）")
    return Decided(question, next(c for c in question.candidates if c.option == answer.choice), answer.probability)


def apply(ontology: Ontology, decided: Sequence[Decided]) -> Ontology:
    """選んだ組を表の主キーにする。表の根拠に主張を足し、確信度は表の確信度と選んだ確信度の小さいほう。
    表の種類（規則の relation の判定）は計算し直さない。種類はこの後の表の分類（判断モデル）が、選んだ主キーを材料に決める。"""
    by_id = {d.question.table.id: d for d in decided}
    tables: list[Table] = []
    for t in ontology.tables:
        d = by_id.get(t.id)
        if d is None:
            tables.append(t)
            continue
        if t.identity is not None:
            raise ValueError(f"主キーのある表に主キーを当てない（問いを作った後に OM が変わった）: {t.name}")
        keyed = t.model_copy(update={"identity": Identity(primary=list(d.candidate.columns)),
                                     "evidence": sorted({*(t.evidence or []), d.candidate.claim.id})})
        tables.append(with_part(keyed, "identity", d.confidence))
    return Ontology(vocabulary=ontology.vocabulary, tables=tuple(tables))


def evidence(question: TableQuestion) -> Mapping[str, list[str]]:
    """問いの ID → 材料にした主張 ID。"""
    return {"primary key": sorted(c.claim.id for c in question.candidates)}
