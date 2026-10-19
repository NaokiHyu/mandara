"""名前の手がかりの無い参照の推定（T2.2 の 3b）：値の包含が 100% でも名前では決まらない列を、判断モデルに問う。

Domain 層の純粋な関数（呼び出しは Application がポート経由で行う）。仕様 6.7.2 B（包含・被覆）。
- 問う列：包含が 100% の測定（included_in）を持つ列のうち、OM の 1 列の関係の参照元でも、宣言された外部キーの列でも、
  表の 1 列のキー（主キー・代替キー）でもない列（_excluded）。キーは自分の ID が偶然、別の表の連番に含まれるだけ
  （主キーが別の表を指すのは名前が一致するときで、それは規則が選ぶ）。宣言の無いソース（ファイル）では、キーの推定が
  この前に来る前提（キーが分からないと、各表の ID の列を毎回問うことになる）
- 参照先の候補：参照先の表に OM の identity があれば、その 1 列のキー（主キー・代替キー）だけ。無ければ（主キーを決められ
  なかった表。T2.7）、測った参照先（データで一意な 1 列も）を全部。データで偶然一意な列（複合主キーの一部など）が候補に
  入ると、本当の参照先の確率が割れる。宣言のあるソースでは、測る参照先がもともと identity の 1 列のキーなので変わらない。
  ほかの表のキーと同じ値の集合で、そのキーへの関係を持つキー（双子の元でない側。mandara.modeler.twins）も参照先にしない
- 問いに書く事実は、主張から確かめたものだけ（宣言が無いことは _excluded で、名前が候補を指さないことは name_strength で）
- 選択肢：参照先の候補を、偶然の確率（chance）が低い順、同じなら最も狭い包含先（キーの値が少ない）の順に MAX_TARGETS 個
  まで ＋「参照ではない」＋「決められない」。切り詰めた数は問いに書く
- 偶然の確率：参照元の異なる値 d 個が、参照元の値の範囲（最小〜最大）の整数 S 個からでたらめに選ばれたとして、すべてが
  その範囲にあるキーの値 K 個に入る確率 C(K, d) / C(S, d)。整数の列（value_span がある）だけ。範囲の中でキーに隙間が無ければ 1
  （包含は何の証拠にもならない。小さい整数はたいていの連番に含まれる）、隙間の多い範囲に多くの値が全部含まれるなら 0 に近い。
  確率は解釈なので主張にはせず、ここで数から計算する。範囲をキー全体ではなく参照元の範囲にとるのは、値が範囲の下の方に
  偏る（1 から始まる数量や区分）と、キー全体の詰まり方では偶然を過小に見積もるため
- 確信度は選んだ選択肢の確率。ただし CONFIDENCE_CAP を超えない（名前の手がかりの無い推定は、規則の推定より弱い根拠。
  人のレビューに回る）。閾値（classify.THRESHOLD）未満と「決められない」は提案しない。理由つきで記録する
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mandara.llm.decision import Choice, ChoiceAnswer, DecisionRequest, DecisionResponse
from mandara.modeler import classify, joins
from mandara.modeler.llm import Dropped
from mandara.modeler.rules import inferred_relation
from mandara.om.model import Claim, ClaimPredicate, Relation, Table, TypeFamily
from mandara.om.names import qualify
from mandara.om.ontology import SELF, Ontology

MODELER = "modeler-nameless-joins 0.2"
"""名前の手がかりの無い包含から、判断モデルが選んだ参照の提案者。問い方や選び方を変えたら版を上げる。"""
CONFIDENCE_CAP = 0.7
"""確信度の上限（規則の推定の最低と同じ。名前の手がかりがあるものより上にしない）。"""
MAX_TARGETS = 5
"""1 つの列の参照先の選択肢の数の上限（偶然の確率が低い順）。"""
NOT_A_REFERENCE = "not_a_reference"


def log10_chance(distinct: int, key_values: int, span: int | None) -> float | None:
    """偶然の確率の常用対数：log10(C(K, d) / C(S, d))。K は範囲にあるキーの値の数、S は範囲の整数の数。
    span が無ければ（整数の列でなければ）None。"""
    if span is None:
        return None
    if not 0 <= distinct <= key_values <= span:
        raise ValueError(f"偶然の確率の前提が崩れている: d={distinct}, K={key_values}, S={span}")
    if key_values == span:
        return 0.0
    # C(K, d) / C(S, d) = Π_{i<d} (K − i) / (S − i)。項ごとに log1p で足す（lgamma の差の打ち消しで 0 を超えないように。
    # どの項も 1 以下なので、和は単調に下がり、0 以下になる）
    return math.fsum(math.log1p((key_values - span) / (span - i)) for i in range(distinct)) / math.log(10)


@dataclass(frozen=True)
class Target:
    claim: Claim
    """参照先の候補の測定（included_in、包含 100%）。"""
    log10_chance: float | None

    @property
    def option(self) -> str:
        t, keys = self.claim.target, self.claim.target_columns or []
        if t is None or len(keys) != 1:
            raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {self.claim.id}")
        return f"{qualify(t.schema_name, t.table)}.{keys[0]}"


@dataclass(frozen=True)
class Asked:
    table: str
    column: str
    targets: tuple[Target, ...]
    """選択肢に出した参照先（偶然の確率が低い順）。"""
    omitted: int
    """選択肢の上限で出さなかった参照先の数。"""
    nameless: bool
    """列の名前が、どの候補（出さなかったものも含む）の表とキーも指していない（joins.name_strength が 0）。"""


@dataclass(frozen=True)
class TableQuestions:
    table: Table
    asked: tuple[Asked, ...]
    request: DecisionRequest


def _rank(t: Target) -> tuple[float, int, str]:
    # 1 つの列の候補は同じ型族（抽出器が型族で組を作る）なので、確率は全部あるか全部無い。無ければキーの値の数と名前で並ぶ
    if t.claim.target_values is None:
        raise ValueError(f"included_in にキーの値の数が無い（主張の検証を通ったはず）: {t.claim.id}")
    return (0.0 if t.log10_chance is None else t.log10_chance, t.claim.target_values, t.option)


def _excluded(table: Table, table_claims: Sequence[Claim]) -> set[str]:
    """問わない列：OM の 1 列の関係の参照元（宣言・規則の推定・人の判断）、宣言された外部キーの列（範囲外を指すものや
    複合の一部も。宣言があるのに推定を重ねない）、表の 1 列のキー（主キーと代替キー。自分の ID が偶然ほかの連番に入るだけ）。"""
    out = {r.via[0].from_ for r in table.relations or [] if r.via and len(r.via) == 1}
    out |= {name for c in table_claims if c.predicate == ClaimPredicate.references.value for name in c.columns or []}
    if table.identity is not None:
        keys = [table.identity.primary or [], *(k.columns for k in table.identity.alternates or [])]
        out |= {k[0] for k in keys if len(k) == 1}
    return out


def same_set(claim: Claim) -> bool:
    """包含の測定が、互いに同じ値の集合を表す（参照元の異なる値がすべてキーにあり、数がキーの値の数と同じ）。"""
    return (claim.predicate == ClaimPredicate.included_in.value and bool(claim.distinct_values)
            and claim.distinct_values == claim.matched_values == claim.target_values)


@dataclass(frozen=True)
class TargetKeys:
    """参照先にしてよい 1 列のキー。"""
    keys: Mapping[str, frozenset[str] | None]
    """表 → identity の 1 列のキー（主キーと代替キー）。identity の無い表は None（測った参照先を全部）。"""
    shared: frozenset[tuple[str, str]]
    """ほかの表のキーと同じ値の集合を持ち、そのキーへの関係の参照元になっているキー（双子の元でない側。
    mandara.modeler.twins）。参照はその先の元の表を指す。"""

    def allows(self, table: str, column: str) -> bool:
        if table not in self.keys:
            raise ValueError(f"参照先 {table} が OM に無い（台帳と OM が食い違う）")
        keys = self.keys[table]
        return (keys is None or column in keys) and (table, column) not in self.shared


def target_keys(ontology: Ontology, claims: Sequence[Claim]) -> TargetKeys:
    same = {c.id for c in claims if same_set(c)}
    keys: dict[str, frozenset[str] | None] = {}
    shared: set[tuple[str, str]] = set()
    for t in ontology.tables:
        if t.identity is None:
            keys[t.name] = None
        else:
            found = [t.identity.primary or [], *(k.columns for k in t.identity.alternates or [])]
            keys[t.name] = frozenset(k[0] for k in found if len(k) == 1)
        shared |= {(t.name, r.via[0].from_) for r in t.relations or []
                   if r.via and len(r.via) == 1 and same & set(r.evidence or [])}
    return TargetKeys(keys, frozenset(shared))


def _allowed(claim: Claim, targets: TargetKeys) -> bool:
    t, keys = claim.target, claim.target_columns or []
    if t is None or len(keys) != 1:
        raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {claim.id}")
    return targets.allows(qualify(t.schema_name, t.table), keys[0])


def questions(ontology: Ontology, claims: Sequence[Claim]) -> list[TableQuestions]:
    """問う列を表ごとにまとめ、表ごとに1つの問い（表の名前の順）。問うものが無ければ空。

    整数の列なのに値の範囲（value_span）の無い測定は、範囲を測る前の抽出器の台帳。黙って「確率は分からない」とせず、
    誤りにする（台帳を作り直す）。"""
    by_table: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_table[qualify(c.subject.schema_name, c.subject.table)].append(c)
    targets = target_keys(ontology, claims)
    out: list[TableQuestions] = []
    for table in sorted(ontology.tables, key=lambda t: t.name):
        table_claims = by_table.get(table.name, [])
        excluded = _excluded(table, table_claims)
        families = {c.subject.column: c.family for c in table_claims if c.predicate == ClaimPredicate.typed_as.value}
        candidates: dict[str, list[Target]] = defaultdict(list)
        for c in table_claims:
            if c.predicate != ClaimPredicate.included_in.value:
                continue
            columns = c.columns or []
            if (len(columns) != 1 or columns[0] in excluded or not c.distinct_values
                    or c.matched_values != c.distinct_values or not _allowed(c, targets)):
                continue
            if c.value_span is None or c.target_values_in_span is None:
                if families.get(columns[0]) == TypeFamily.integer.value:
                    raise ValueError(f"整数の列の包含の測定に値の範囲が無い（範囲を測る前の台帳。抽出し直す）: {c.id}")
                chance = None
            else:
                chance = log10_chance(c.distinct_values, c.target_values_in_span, c.value_span)
            candidates[columns[0]].append(Target(c, chance))
        if not candidates:
            continue
        asked = tuple(Asked(table.name, name, tuple(sorted(ts, key=_rank)[:MAX_TARGETS]), max(0, len(ts) - MAX_TARGETS),
                            _nameless(name, ts))
                      for name, ts in sorted(candidates.items()))
        material = classify.state(joins.claims_for(table, table_claims))
        out.append(TableQuestions(table, asked, DecisionRequest(
            f"nameless joins {table.name}", material, {f"column {a.column}": _choice(a) for a in asked})))
    return out


def _nameless(column: str, targets: Sequence[Target]) -> bool:
    out = True
    for t in targets:
        c = t.claim
        if c.target is None or len(c.target_columns or []) != 1:
            raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {c.id}")
        out = out and joins.name_strength(column, c.target.table, (c.target_columns or [""])[0]) == 0
    return out


def _chance_text(log10: float | None) -> str:
    if log10 is None:
        return "the values are not integers, so the chance of a coincidence is not known"
    if log10 > -2:
        return f"the chance that this inclusion is a coincidence is {10 ** log10:.2f}"
    return f"the chance that this inclusion is a coincidence is about 10^{math.floor(log10)}"


def _choice(a: Asked) -> Choice:
    first = a.targets[0].claim
    rows = f"each value appears in at most {first.max_repeats} rows of the `table`"
    span = (f" They lie in a range of {first.value_span} consecutive integers." if first.value_span is not None else "")
    options: dict[str, str | None] = {}
    for t in a.targets:
        c = t.claim
        within = (f"; {c.target_values_in_span} of its key values lie in that range" if c.target_values_in_span is not None
                  else "")
        options[t.option] = (f"`column` refers to this key: all {c.distinct_values} distinct values of `column` are among "
                             f"its {c.target_values} key values{within}; {_chance_text(t.log10_chance)}.")
    options[NOT_A_REFERENCE] = ("`column` does not refer to another table: its values (counts, quantities, codes, "
                                "levels, sequence numbers) only happen to fall among the key values.")
    options[classify.UNCLEAR] = "The facts do not decide it."
    note = f" {a.omitted} more tables whose keys also contain every value are not listed." if a.omitted else ""
    return Choice({"column": a.column,
                   "facts": [f"{first.distinct_values} distinct values; {rows}.{span}",
                             "No foreign key is declared on `column`."
                             + (" Its name does not name any of the candidate tables or keys." if a.nameless else "")
                             + note],
                   "question": "Does `column` of the `table` hold references to rows of another table (a foreign key "
                               "that the database does not declare)? If so, which key? Judge from the names and "
                               "comments of the table and column, what the column means, and how unlikely the "
                               "inclusion is by coincidence."},
                  options)


@dataclass(frozen=True)
class Decided:
    asked: Asked
    target: Target
    probability: float

    @property
    def confidence(self) -> float:
        return min(self.probability, CONFIDENCE_CAP)


@dataclass(frozen=True)
class Read:
    decided: tuple[Decided, ...]
    not_references: tuple[str, ...]
    """判断モデルが参照ではないと答えた列（「表.列」。閾値以上のもの）。"""
    abstained: tuple[Dropped, ...]


def read(asked: TableQuestions, response: DecisionResponse) -> Read:
    decided: list[Decided] = []
    not_references: list[str] = []
    abstained: list[Dropped] = []
    for a in asked.asked:
        key = f"column {a.column}"
        answer: ChoiceAnswer = response.answers[key]
        item = f"{a.table}.{a.column}"
        if answer.choice == classify.UNCLEAR:
            abstained.append(Dropped(item, f"判断モデルが決められないと答えた（確率 {answer.probability:.2f}）"))
        elif answer.probability < classify.THRESHOLD:
            abstained.append(Dropped(item, f"確信度 {answer.probability:.2f} が閾値 {classify.THRESHOLD} 未満（{answer.choice}）"))
        elif answer.choice == NOT_A_REFERENCE:
            not_references.append(item)
        else:
            target = next(t for t in a.targets if t.option == answer.choice)
            decided.append(Decided(a, target, answer.probability))
    return Read(tuple(decided), tuple(not_references), tuple(abstained))


def apply(ontology: Ontology, decided: Sequence[Decided], inferred_by: str) -> Ontology:
    """選んだ参照を OM の関係にする。"""
    ids = {t.name: t.id for t in ontology.tables}
    added: dict[str, list[Relation]] = defaultdict(list)
    for d in decided:
        c = d.target.claim
        if c.target is None:
            raise ValueError(f"included_in に target が無い（主張の検証を通ったはず）: {c.id}")
        target_name = qualify(c.target.schema_name, c.target.table)
        if target_name not in ids:
            raise ValueError(f"推定した参照の参照先 {target_name} が OM に無い（台帳と OM が食い違う）: {c.id}")
        table_id = ids[d.asked.table]
        added[table_id].append(inferred_relation(c, SELF if target_name == d.asked.table else ids[target_name],
                                                 c.max_repeats == 1, d.confidence, inferred_by))
    tables = tuple(t.model_copy(update={"relations": [*(t.relations or []), *added[t.id]]}) if added.get(t.id) else t
                   for t in ontology.tables)
    return Ontology(vocabulary=ontology.vocabulary, tables=tables)


def evidence(asked: TableQuestions) -> Mapping[str, list[str]]:
    """問いの ID → 材料にした測定の主張 ID。"""
    return {f"column {a.column}": sorted(t.claim.id for t in a.targets) for a in asked.asked}
