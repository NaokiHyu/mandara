"""値の似た列の組が同じ意味かを、判断モデルに組ごとに問う（T2.6 の E。2026-10-19、ユーザーと合意）。

Domain 層の純粋な関数（呼び出しは Application がポート経由で行う）。
- 表の分類（mandara.modeler.classify）の問いには、値の似た列の事実を混ぜない。混ぜると、名前で決まるはずの判断が別の意味型に
  引かれて確率が割れる（段 2 の測定：受注日が更新日時に引かれ、column_types が 116 → 97）。似かたは、表の分類がすべて済んだ
  後に、この別の問いで使う
- 問う組：値の似かた（resembles）のうち、MIN_CLOSE 個以上（すべて）の信号が近いと言う組で、両方の列が OM にあり、人が判断していない
  （proposed）。片方にだけ意味型があるか、両方にあって違う組。意味型を受け取る側（receiver）は、意味型の無い側。両方にあれば、
  使う列の少ない意味型の側（同じなら名前の順で後ろ）。受け取る側が次のどれかなら問わない（数だけ返す）：
  - 外部キーで同じ意味の組（mandara.modeler.meaning）に入っている（組の意味型は組で決める。1 列だけ変えると食い違う）
  - 渡す意味型を、型族・encoding で当てられない（misfit）、または単位の要る意味型（単位を決められない。伝播と同じ）
  - 渡す意味型が時刻の役割を持ち、受け取る列の表では使われていない（classify.brings_another_rows_role。分類の選択肢と同じ規則）
- 問いは表ごと（意味型を受け取る列の表）に 1 回。材料は両方の列の名前・SQL の型・値の形・意味型と、信号ごとの事実
- 確信度は判断モデルの確率。ただし CONFIDENCE_CAP を超えない。閾値（classify.THRESHOLD）未満、「違う」「決められない」は
  当てない。理由つきで記録する
- 当てるのは、互いに食い違わない答えだけ（apply）。組は段の前の OM で作るので、ある答えの受け取る列が別の答えの渡す側だと、
  渡す意味型が古くなる（連鎖）。1 つの列に別々の意味型を渡す答えは矛盾。どちらも当てずに、理由つきで記録する
- 受け取る列にすでに意味型があれば上書きする。そのとき前の根拠は持ち越さない（新しい判断を支えないので）。上書きした列は
  元の意味型と一緒に記録し、使う列が無くなった意味型も記録する（語彙には残る）
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mandara.llm.decision import Choice, DecisionRequest, DecisionResponse
from mandara.modeler import classify
from mandara.modeler.llm import Dropped
from mandara.modeler.meaning import MeaningGroups, Member, label
from mandara.modeler.resemblance import SHAPE_NOTATION, Reading, Resemblances, value_shape
from mandara.om.invariants import check
from mandara.om.model import Claim, ClaimPredicate, Column, ReviewStatus
from mandara.om.names import qualify
from mandara.om.ontology import Ontology
from mandara.om.value_types import misfit
from mandara.resemblance.signals import SIGNALS

MODELER = "modeler-same-meaning 0.1"
CONFIDENCE_CAP = 0.7
"""確信度の上限（値の似かたは名前の手がかりより弱い根拠。人のレビューに回る）。"""
MIN_CLOSE = len(SIGNALS)
"""問う組に要る、近いと言う信号の数：すべての信号（3 つの一致で適合率 75%、2 つで 61%。2026-10-18 の測定）。2 つで問うと
1 回に 300〜400 組を問い、ほとんどが本当に違う意味の整数の番号どうしだった（2026-10-19。費用だけかかる）。測っていない
信号のある組（重い信号の候補に入らなかった組、値の少ない列）は問わない。"""
SAME, DIFFERENT = "same", "different"
_OPTIONS: dict[str, str | None] = {
    SAME: "The two columns hold the same kind of value with the same meaning; they share one semantic type.",
    DIFFERENT: "The values only look alike; the columns mean different things (an order date and a ship date, "
               "an order number and an invoice number).",
    classify.UNCLEAR: "The facts do not decide it.",
}


@dataclass(frozen=True)
class Pair:
    receiver: Member
    donor: Member
    semantic_type: str
    """受け取る側に付ける、相手の意味型。"""
    readings: tuple[Reading, ...]
    """組の信号ごとの測定。"""

    @property
    def close(self) -> int:
        return sum(r.close for r in self.readings)

    @property
    def key(self) -> str:
        return f"same {label(self.receiver)} ~ {label(self.donor)}"


@dataclass(frozen=True)
class TableQuestions:
    table: str
    pairs: tuple[Pair, ...]
    request: DecisionRequest
    evidence: Mapping[str, tuple[str, ...]]
    """問いの ID → 根拠の主張 ID（信号の測定と、両方の列の型・値の形の主張）。"""


@dataclass(frozen=True)
class Asked:
    tables: tuple[TableQuestions, ...]
    not_asked: tuple[Dropped, ...]
    """問わなかった組（近い組のうち、外部キーの組・型が合わない・単位の要る意味型・人の判断）と理由。"""


def _columns(ontology: Ontology) -> dict[Member, Column]:
    return {(t.name, name): c for t in ontology.tables for name, c in (t.columns or {}).items()}


def questions(ontology: Ontology, claims: Sequence[Claim], found: Resemblances, groups: MeaningGroups,
              families: Mapping[Member, str]) -> Asked:
    columns = _columns(ontology)
    vocabulary = ontology.vocabulary.semantic_types or {}
    uses = Counter(c.semantic_type for c in columns.values() if c.semantic_type is not None)
    tables_of: dict[str, set[str]] = defaultdict(set)
    for (table, _), c in columns.items():
        if c.semantic_type is not None:
            tables_of[c.semantic_type].add(table)

    def elsewhere_only(table: str) -> set[str]:
        return {k for k, tables in tables_of.items() if table not in tables}

    pairs: dict[str, list[Pair]] = defaultdict(list)
    not_asked: list[Dropped] = []
    seen: set[tuple[Member, Member]] = set()
    for member in sorted(found.by_member):
        for n in found.of(member):
            a, b = sorted((member, n.member))
            if (a, b) in seen or n.close < MIN_CLOSE:
                continue
            seen.add((a, b))
            ca, cb = columns.get(a), columns.get(b)
            if ca is None or cb is None:
                continue  # 範囲の外（OM に無い列）
            ta, tb = ca.semantic_type, cb.semantic_type
            if ta == tb:
                continue  # 同じ意味型か、どちらにも無い（渡すものが無い）
            if tb is None or (ta is not None and (uses[ta], b) > (uses[tb], a)):
                receiver, donor, kind = b, a, ta
            else:
                receiver, donor, kind = a, b, tb
            assert kind is not None
            item = f"same {label(receiver)} ~ {label(donor)}"
            receiving = columns[receiver]
            if receiving.status != ReviewStatus.proposed.value:
                not_asked.append(Dropped(item, f"受け取る列は人が判断済み（{receiving.status}）"))
                continue
            group = groups.of(*receiver)
            if group is not None and len(group.members) > 1:
                not_asked.append(Dropped(item, "受け取る列は外部キーで同じ意味の組に入っている（組の意味型は組で決める）"))
                continue
            family = families.get(receiver)
            if family is None:
                not_asked.append(Dropped(item, "受け取る列の型の主張が無い"))
                continue
            semantic_type = vocabulary[kind]
            if (reason := misfit(family, receiving.encoding, semantic_type)) is not None:
                not_asked.append(Dropped(item, reason))
                continue
            if semantic_type.requires_unit:
                not_asked.append(Dropped(item, "単位の要る意味型は、単位を決められないので渡さない"))
                continue
            if classify.brings_another_rows_role(semantic_type, elsewhere_only(receiver[0])):
                not_asked.append(Dropped(item, f"時刻の役割（{semantic_type.role}）を持つ意味型を、使っていない表に渡さない"))
                continue
            pairs[receiver[0]].append(Pair(receiver, donor, kind, n.readings))
    by_member = _by_member(claims)
    tables = tuple(_table(table, tuple(ps), ontology, by_member) for table, ps in sorted(pairs.items()))
    return Asked(tables, tuple(not_asked))


def _by_member(claims: Sequence[Claim]) -> dict[Member, list[Claim]]:
    out: dict[Member, list[Claim]] = defaultdict(list)
    for c in claims:
        if c.subject.column is not None and c.predicate in (ClaimPredicate.typed_as.value, ClaimPredicate.shaped_as.value,
                                                            ClaimPredicate.described_as.value):
            out[(qualify(c.subject.schema_name, c.subject.table), c.subject.column)].append(c)
    return out


def _describe(member: Member, column: Column, own: Sequence[Claim]) -> dict[str, object]:
    out: dict[str, object] = {"column": label(member), "semantic_type": column.semantic_type or "none yet"}
    for c in own:
        if c.predicate == ClaimPredicate.typed_as.value:
            out["sql_type"] = c.type_name
        elif c.predicate == ClaimPredicate.shaped_as.value:
            out["value_shape"] = value_shape(c)
        elif c.predicate == ClaimPredicate.described_as.value:
            out["comment"] = c.text
    return out


def _table(table: str, pairs: tuple[Pair, ...], ontology: Ontology,
           by_member: Mapping[Member, list[Claim]]) -> TableQuestions:
    columns = _columns(ontology)
    vocabulary = ontology.vocabulary.semantic_types or {}
    questions: dict[str, Choice] = {}
    evidence: dict[str, tuple[str, ...]] = {}
    kinds: set[str] = set()
    for p in pairs:
        described = [_describe(m, columns[m], by_member.get(m, [])) for m in (p.receiver, p.donor)]
        kinds |= {k for k in (columns[p.receiver].semantic_type, p.semantic_type) if k is not None}
        questions[p.key] = Choice({
            "columns": described,
            "facts": [r.fact for r in p.readings],
            "question": "Do the two `columns` mean the same thing, so that they share one semantic type? Their values "
                        f"look alike: {p.close} of the {len(p.readings)} measures taken find them as "
                        "similar as values within one column. Judge the meaning by the names and the table as well; "
                        "values of the same shape often mean different things."}, _OPTIONS)
        evidence[p.key] = tuple(sorted({*(r.claim for r in p.readings),
                                        *(c.id for m in (p.receiver, p.donor) for c in by_member.get(m, []))}))
    state: dict[str, object] = {
        "semantic_types": {k: f"{vocabulary[k].description or ''} (value type {vocabulary[k].type})".strip()
                           for k in sorted(kinds)},
        "value_shape_notation": SHAPE_NOTATION}
    return TableQuestions(table, pairs, DecisionRequest(f"same meaning {table}", state, questions), evidence)


@dataclass(frozen=True)
class Decided:
    pair: Pair
    probability: float
    evidence: tuple[str, ...]

    @property
    def confidence(self) -> float:
        return min(self.probability, CONFIDENCE_CAP)


@dataclass(frozen=True)
class Read:
    decided: tuple[Decided, ...]
    different: tuple[str, ...]
    """判断モデルが違う意味と答えた組（閾値以上）。"""
    abstained: tuple[Dropped, ...]


def read(asked: TableQuestions, response: DecisionResponse) -> Read:
    decided: list[Decided] = []
    different: list[str] = []
    abstained: list[Dropped] = []
    for p in asked.pairs:
        answer = response.answers[p.key]
        if answer.choice == classify.UNCLEAR:
            abstained.append(Dropped(p.key, f"判断モデルが決められないと答えた（確率 {answer.probability:.2f}）"))
        elif answer.probability < classify.THRESHOLD:
            abstained.append(Dropped(p.key, f"確信度 {answer.probability:.2f} が閾値 {classify.THRESHOLD} 未満"
                                            f"（{answer.choice}）"))
        elif answer.choice == DIFFERENT:
            different.append(p.key)
        else:
            decided.append(Decided(p, answer.probability, asked.evidence[p.key]))
    return Read(tuple(decided), tuple(different), tuple(abstained))


@dataclass(frozen=True)
class Applied:
    ontology: Ontology
    """当てた OM。violations があれば、入力の OM のまま（何も当てない）。"""
    changed: tuple[str, ...]
    """「表.列 意味型」。上書きなら「表.列 意味型（元: 前の意味型）」。"""
    skipped: tuple[Dropped, ...]
    orphaned: tuple[str, ...]
    """上書きで、使う列が無くなった意味型（語彙には残る）。"""
    violations: tuple[str, ...]
    """当てた結果が検証（4.5）で生んだ新しい違反。問う前の確かめの漏れで、バグ（呼ぶ側が止める）。"""


def apply(ontology: Ontology, decided: Sequence[Decided], inferred_by: str) -> Applied:
    """互いに食い違わない答えだけを当てる。入力の順に依らない。

    当てない答え（理由つきで skipped）：受け取る列がすでに相手の意味型を持つ／受け取る列が別の答えの渡す側（連鎖）／
    渡す側が別の答えで変わる（連鎖）／1 つの列に別々の意味型を渡す答えがある（矛盾）。"""
    columns = _columns(ontology)
    donors = {d.pair.donor for d in decided}
    receivers = {d.pair.receiver for d in decided}
    kinds: dict[Member, set[str]] = defaultdict(set)
    for d in decided:
        kinds[d.pair.receiver].add(d.pair.semantic_type)
    skipped: list[Dropped] = []
    chosen: dict[Member, Decided] = {}
    for d in sorted(decided, key=lambda d: (-d.probability, d.pair.key)):
        receiver, donor = d.pair.receiver, d.pair.donor
        if columns[receiver].semantic_type == d.pair.semantic_type:
            skipped.append(Dropped(d.pair.key, "受け取る列は、すでに相手の意味型を持つ"))
        elif receiver in donors:
            skipped.append(Dropped(d.pair.key, "受け取る列が、別の答えで意味型を渡す側でもある（連鎖。渡す意味型が食い違う）"))
        elif donor in receivers:
            skipped.append(Dropped(d.pair.key, "渡す側の意味型が、別の答えで変わる（連鎖）"))
        elif len(kinds[receiver]) > 1:
            skipped.append(Dropped(d.pair.key, f"1 つの列に別々の意味型を渡す答えがある（矛盾: {sorted(kinds[receiver])}）"))
        elif receiver in chosen:
            skipped.append(Dropped(d.pair.key, "同じ列に同じ意味型を渡す、確率のより高い答えで当てた"))
        else:
            chosen[receiver] = d
    updates: dict[str, dict[str, Column]] = defaultdict(dict)
    changed: list[str] = []
    for receiver, d in sorted(chosen.items()):
        current = columns[receiver]
        updates[receiver[0]][receiver[1]] = current.model_copy(update={
            "semantic_type": d.pair.semantic_type, "confidence": d.confidence, "inferred_by": inferred_by,
            "evidence": list(d.evidence)})
        before = f"（元: {current.semantic_type}）" if current.semantic_type is not None else ""
        changed.append(f"{label(receiver)} {d.pair.semantic_type}{before}")
    if not updates:
        return Applied(ontology, (), tuple(skipped), (), ())
    tables = tuple(t.model_copy(update={"columns": {**(t.columns or {}), **updates[t.name]}})
                   if t.name in updates else t for t in ontology.tables)
    result = Ontology(ontology.vocabulary, tables)
    before_violations = set(check(ontology))
    if introduced := [v for v in check(result) if v not in before_violations]:
        return Applied(ontology, (), tuple(skipped), (), tuple(str(v) for v in introduced))
    used = {c.semantic_type for c in _columns(result).values() if c.semantic_type is not None}
    replaced = {k for k in (columns[r].semantic_type for r in chosen) if k is not None}
    return Applied(result, tuple(changed), tuple(skipped), tuple(sorted(replaced - used)), ())
