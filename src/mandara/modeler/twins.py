"""双子のキー：値の集合がまったく同じ 1 列のキーの組（T2.7）。どれが元の表か、偶然かを判断モデルに問う。

Domain 層の純粋な関数（呼び出しは Application がポート経由で行う）。
- 双子：参照先になりうる 1 列のキー（mandara.modeler.nameless_joins.target_keys）どうしで、包含が両向きに 100% で、値の数が
  同じ（nameless_joins.same_set）。つながった組（A=B、B=C）を 1 つの組にする
- 本物の双子は、ほかの表に 1 対 1 で付く表が、その表の ID を自分のキーにしているもの（productproductphoto の製品 ID）。
  偶然の双子は、行に 1, 2, 3, ... と番号を振った小さな表どうし。データの事実（値の集合）だけでは、どちらが元かは決まらない
  （ほかの表から参照される数も必ず同じ）。なので名前と材料から判断モデルに問う
- 外部キーを 1 本でも宣言しているソースでは問わない（not_declaring）。宣言のあるソースでは「外部キーが無い」こと自体が
  強い事実で、本物の双子は外部キーで結ばれている（問うのは偶然の双子だけになり、費用と誤った 1 対 1 の危険だけが残る）
- 問わない組：どれかのキーの列が、すでに OM の関係の参照元（宣言・推定・人の判断）。組の形はもう決まっている
- 未決：元でない側のキーを 3b の参照先から外すかは、関係の根拠が same_set の測定かで決めている（nameless_joins.target_keys）。
  宣言された外部キーの 1 対 1 は外れない。宣言のあるソースではこの段を問わないので今は割れないが、関係の意味でそろえるかは別の課題
- 選択肢：組の表それぞれ ＋「偶然」＋「決められない」。表の数が上限を超える組は問わずに記録する
- 事実：値の数と、偶然の確率（nameless_joins.log10_chance。組の中で最も偶然らしい対のもの。どれかが整数でなければ不明）
- 元の表と答えたら、ほかの表のキーから元の表のキーへの 1 対 1 の関係を足す（確信度は確率、CONFIDENCE_CAP を超えない）。
  元でない表のキーは、それ以後、参照先の候補にならない（nameless_joins.target_keys が same_set の関係を見る）
- 閾値（classify.THRESHOLD）未満・「偶然」・「決められない」は何もしない。理由つきで記録する
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mandara.llm.decision import Choice, DecisionRequest, DecisionResponse
from mandara.modeler import classify, joins, nameless_joins
from mandara.modeler.llm import Dropped
from mandara.modeler.rules import inferred_relation
from mandara.om.model import Claim, ClaimPredicate, Relation, TypeFamily
from mandara.om.names import qualify
from mandara.om.ontology import Ontology

MODELER = "modeler-twins 0.1"
"""双子のキーの元の表を、判断モデルが選んだ関係の提案者。問い方や選び方を変えたら版を上げる。"""
CONFIDENCE_CAP = 0.7
MAX_MEMBERS = 5
COINCIDENCE = "coincidence"

Key = tuple[str, str]
"""（表の名前、列）。"""


@dataclass(frozen=True)
class Group:
    members: tuple[Key, ...]
    """表の名前の順。"""
    inclusions: Mapping[tuple[Key, Key], Claim]
    """（元、先）→ 包含の測定（same_set）。組の中のつながった対の両向き。"""
    request: DecisionRequest

    @property
    def name(self) -> str:
        return " = ".join(f"{t}.{c}" for t, c in self.members)


def _key(claim: Claim) -> tuple[Key, Key]:
    t, columns, keys = claim.target, claim.columns or [], claim.target_columns or []
    if t is None or len(columns) != 1 or len(keys) != 1:
        raise ValueError(f"included_in の形が崩れている（主張の検証を通ったはず）: {claim.id}")
    return (qualify(claim.subject.schema_name, claim.subject.table), columns[0]), (qualify(t.schema_name, t.table), keys[0])


def declared_references(claims: Sequence[Claim]) -> bool:
    """ソースが外部キーを 1 本でも宣言している（この段を問わない）。"""
    return any(c.predicate == ClaimPredicate.references.value for c in claims)


def questions(ontology: Ontology, claims: Sequence[Claim]) -> tuple[list[Group], list[Dropped]]:
    """問う組（組の名前の順）と、問わなかった組（理由つき）。外部キーを宣言しているソースには使わない（呼ぶ側が
    declared_references で確かめる。ここでは誤りにする）。"""
    if declared_references(claims):
        raise ValueError("外部キーを宣言しているソースでは双子のキーを問わない（declared_references で確かめてから呼ぶ）")
    targets = nameless_joins.target_keys(ontology, claims)
    tables = {t.name: t for t in ontology.tables}
    edges: dict[tuple[Key, Key], Claim] = {}
    for c in claims:
        if not nameless_joins.same_set(c):
            continue
        a, b = _key(c)
        if a[0] != b[0] and targets.allows(*a) and targets.allows(*b):
            edges[(a, b)] = c
    parent: dict[Key, Key] = {}

    def find(k: Key) -> Key:
        while parent.setdefault(k, k) != k:
            k = parent[k]
        return k

    for a, b in edges:
        if (b, a) in edges:  # 両向きの測定がある対だけ（キーどうしは両向きに測る）
            parent[find(a)] = find(b)
    components: dict[Key, set[Key]] = defaultdict(set)
    for a, b in edges:
        if (b, a) in edges:
            components[find(a)] |= {a, b}
    by_table: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_table[qualify(c.subject.schema_name, c.subject.table)].append(c)
    families: dict[Key, str | None] = {(qualify(c.subject.schema_name, c.subject.table), c.subject.column): c.family
                                       for c in claims if c.predicate == ClaimPredicate.typed_as.value
                                       and c.subject.column is not None}
    out: list[Group] = []
    skipped: list[Dropped] = []
    for component in components.values():
        members = tuple(sorted(component))
        inclusions = {(a, b): c for (a, b), c in edges.items() if a in component and b in component and (b, a) in edges}
        name = " = ".join(f"{t}.{c}" for t, c in members)
        linked = [f"{t}.{c}" for t, c in members
                  if any(r.via and len(r.via) == 1 and r.via[0].from_ == c for r in tables[t].relations or [])]
        if linked:
            skipped.append(Dropped(name, f"キーの列がすでに関係の参照元: {linked}"))
            continue
        if len(members) > MAX_MEMBERS:
            skipped.append(Dropped(name, f"表が {len(members)} 個で、選択肢の上限 {MAX_MEMBERS} を超える"))
            continue
        if len({t for t, _ in members}) != len(members):
            skipped.append(Dropped(name, "同じ表の 2 つのキーが組にある（元の表を表の名前で答えられない）"))
            continue
        material = {"tables": [classify.state(joins.claims_for(tables[t], by_table[t])) for t in dict.fromkeys(
            t for t, _ in members)]}
        out.append(Group(members, inclusions, DecisionRequest(f"twins {name}", material,
                                                              {"owner": _choice(members, inclusions, families)})))
    return sorted(out, key=lambda g: g.name), sorted(skipped, key=lambda d: d.item)


def _chance(inclusions: Mapping[tuple[Key, Key], Claim], families: Mapping[Key, str | None]) -> str:
    """組で最も偶然らしい対の偶然の確率。整数でないキーがあれば不明。整数のキーなのに範囲の無い測定は、範囲を測る前の
    台帳なので誤り（nameless_joins.questions と同じ）。"""
    logs: list[float] = []
    for (a, b), c in inclusions.items():
        if c.value_span is None or c.target_values_in_span is None:
            if TypeFamily.integer.value in (families.get(a), families.get(b)):
                raise ValueError(f"整数のキーの包含の測定に値の範囲が無い（範囲を測る前の台帳。抽出し直す）: {c.id}")
            return "not known (the keys are not integers)"
        if c.distinct_values is None:
            raise ValueError(f"included_in に値の数が無い（主張の検証を通ったはず）: {c.id}")
        log10 = nameless_joins.log10_chance(c.distinct_values, c.target_values_in_span, c.value_span)
        if log10 is None:
            raise AssertionError(f"範囲があるのに偶然の確率が無い: {c.id}")
        logs.append(log10)
    worst = max(logs)
    return f"{10 ** worst:.2f}" if worst > -2 else f"about 10^{math.floor(worst)}"


def _choice(members: Sequence[Key], inclusions: Mapping[tuple[Key, Key], Claim],
            families: Mapping[Key, str | None]) -> Choice:
    first = next(iter(inclusions.values()))
    options: dict[str, str | None] = {
        t: f"The ids belong to `{t}`; the rows of the other tables attach to its rows one to one (they share its key)."
        for t, _ in members}
    options[COINCIDENCE] = ("These are separate things whose keys only happen to hold the same values (for example, "
                            "each numbers its rows 1, 2, 3, ...).")
    options[classify.UNCLEAR] = "The facts do not decide it."
    return Choice({"facts": [f"The keys {', '.join(f'{t}.{c}' for t, c in members)} hold exactly the same "
                             f"{first.distinct_values} values. The chance that keys of these sizes hold the same values "
                             f"by coincidence is {_chance(inclusions, families)}."],
                   "question": "Do these keys identify the same things? If so, which table do the ids belong to (the "
                               "others extend it)? Judge from the names and comments of the tables, their columns, "
                               "and the chance of a coincidence."},
                  options)


@dataclass(frozen=True)
class Decided:
    group: Group
    owner: str
    probability: float

    @property
    def confidence(self) -> float:
        return min(self.probability, CONFIDENCE_CAP)


def read(group: Group, response: DecisionResponse) -> Decided | Dropped:
    answer = response.answers["owner"]
    if answer.choice == classify.UNCLEAR:
        return Dropped(group.name, f"判断モデルが決められないと答えた（確率 {answer.probability:.2f}）")
    if answer.probability < classify.THRESHOLD:
        return Dropped(group.name, f"確信度 {answer.probability:.2f} が閾値 {classify.THRESHOLD} 未満（{answer.choice}）")
    if answer.choice == COINCIDENCE:
        return Dropped(group.name, f"判断モデルが偶然と答えた（確率 {answer.probability:.2f}）")
    return Decided(group, answer.choice, answer.probability)


@dataclass(frozen=True)
class Links:
    added: tuple[tuple[Key, Key, Claim], ...]
    """足す関係：（元でないキー、元のキー、その包含の測定）。"""
    missing: tuple[Key, ...]
    """元のキーへ直接の測定が無いキー（A=B、B=C で A が元のときの C）。関係は足さない（根拠の測定が無い）。記録に出す。"""


def links(decided: Decided) -> Links:
    owner = next(k for k in decided.group.members if k[0] == decided.owner)
    added = tuple((a, b, c) for (a, b), c in sorted(decided.group.inclusions.items()) if b == owner)
    linked = {a for a, _, _ in added}
    return Links(added, tuple(k for k in decided.group.members if k != owner and k not in linked))


def apply(ontology: Ontology, decided: Sequence[Decided], inferred_by: str) -> Ontology:
    ids = {t.name: t.id for t in ontology.tables}
    added: dict[str, list[Relation]] = defaultdict(list)
    for d in decided:
        for (table, _), _, c in links(d).added:
            if c.max_repeats != 1:  # 値の数がキーの値の数と同じ（same_set）なら、各値は 1 行
                raise ValueError(f"双子のキーの値が 1 行に収まらない（same_set なのに）: {c.id}")
            added[ids[table]].append(inferred_relation(c, ids[d.owner], True, d.confidence, inferred_by))
    tables = tuple(t.model_copy(update={"relations": [*(t.relations or []), *added[t.id]]}) if added.get(t.id) else t
                   for t in ontology.tables)
    return Ontology(vocabulary=ontology.vocabulary, tables=tables)


def evidence(group: Group) -> Mapping[str, list[str]]:
    return {"owner": sorted(c.id for c in group.inclusions.values())}
