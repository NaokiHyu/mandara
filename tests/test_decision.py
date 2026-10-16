"""判断モデルの層の法則。本物の API は呼ばない（呼ぶのは mandara llm-check だけ）。

- 答えの検証：問いと同じ ID、選択肢の中の値、すべての選択肢の確率（0〜1、和は丸めの誤差まで 1）。外れたら InvalidOutputError
- Jev の実装：HTTP の失敗（429 を含む）は再送せずに LlmError、形の外れた答えはエラー、選択肢の上限を超える問いは送らない
- 予算：LLM と判断モデルが1つの予算を共有し、合計で上限を守る。見積もりが崩れたら、どちらももう送らない
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.llm.decision import (
    BudgetedDecisionModel,
    Choice,
    ChoiceAnswer,
    DecisionRequest,
    DecisionResponse,
    check_answers,
)
from mandara.llm.model import (
    Budget,
    BudgetedModel,
    BudgetExceededError,
    EstimateViolatedError,
    InvalidOutputError,
    LlmError,
    PriceTier,
    Pricing,
    StructuredRequest,
    StructuredResponse,
    Usage,
)
from mandara.llm.typesafe.model import MAX_OPTIONS, JevModel

USAGE = Usage(10, 0)


def _request(*questions: tuple[str, list[str]]) -> DecisionRequest:
    return DecisionRequest("t", {"s": "x"}, {k: Choice("q", {o: None for o in options}) for k, options in questions})


@st.composite
def requests_and_answers(draw: st.DrawFn) -> tuple[DecisionRequest, dict[str, ChoiceAnswer]]:
    keys = draw(st.lists(st.text("abc", min_size=1, max_size=3), min_size=1, max_size=4, unique=True))
    questions: list[tuple[str, list[str]]] = []
    answers: dict[str, ChoiceAnswer] = {}
    for key in keys:
        options = draw(st.lists(st.text("xyz", min_size=1, max_size=3), min_size=2, max_size=6, unique=True))
        weights = draw(st.lists(st.integers(0, 100), min_size=len(options), max_size=len(options)).filter(any))
        probabilities = {o: round(w / sum(weights), 2) for o, w in zip(options, weights, strict=True)}
        questions.append((key, options))
        answers[key] = ChoiceAnswer(max(probabilities, key=lambda o: probabilities[o]), probabilities)
    return _request(*questions), answers


@given(requests_and_answers())
def test_well_formed_answers_pass_and_any_break_is_an_error(case: tuple[DecisionRequest,
                                                                         dict[str, ChoiceAnswer]]) -> None:
    request, answers = case
    check_answers(request, answers, USAGE, None, 0.05)  # 丸めた確率でも通る
    key = next(iter(answers))
    a = answers[key]
    broken = [
        {k: v for k, v in answers.items() if k != key},  # 答えが欠ける
        {**answers, "extra?": a},  # 問うていない答え
        {**answers, key: ChoiceAnswer("not-an-option", {**a.probabilities, "not-an-option": 0.0})},
        {**answers, key: ChoiceAnswer(a.choice, {o: p for o, p in a.probabilities.items() if o != a.choice}
                                      | {a.choice: 1.5})},
        {**answers, key: ChoiceAnswer(a.choice, {o: 0.9 for o in a.probabilities})},  # 和が 1 から遠い
    ]
    for answer in broken:
        with pytest.raises(InvalidOutputError):
            check_answers(request, answer, USAGE, None, 0.05)


# ---------------------------------------------------------------- Jev の実装


def _jev(handler: Any) -> JevModel:
    return JevModel("jev-1.13.0", httpx2.Client(transport=httpx2.MockTransport(handler)))


def _reply(body: object, status: int = 200) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body, headers={"x-typesafe-request-id": "req_1"})
    return handler


GOOD = {"model": "jev-1.13.0", "usage": {"input_tokens": 400, "output_tokens": 30},
        "answers": {"kind": {"type": "choice", "choice": "b", "confidence": 0.7,
                             "probabilities": {"a": 0.1, "b": 0.85, "c": 0.05}}}}


def test_jev_sends_choices_and_reads_answers() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=GOOD, headers={"x-typesafe-request-id": "req_1"})

    response = _jev(handler).decide(_request(("kind", ["a", "b", "c"])))
    assert sent[0]["model"] == "jev-1.13.0" and sent[0]["state"] == {"s": "x"}
    assert sent[0]["questions"]["kind"] == {"type": "choice", "instructions": "q", "criteria": {"a": None, "b": None,
                                                                                               "c": None}}
    assert response.answers["kind"].choice == "b" and response.answers["kind"].probability == 0.85
    assert (response.usage, response.model, response.request_id) == (Usage(400, 30), "jev-1.13.0", "req_1")


@pytest.mark.parametrize(("body", "reason"), [
    ({**GOOD, "answers": {}}, "合わない"),
    ({**GOOD, "answers": {"kind": {"type": "noul", "noul": 0.3}}}, "Choice"),
    ({**GOOD, "answers": {"kind": {"type": "choice", "choice": "z", "probabilities": {"a": 1.0, "b": 0, "c": 0}}}},
     "選択肢の外"),
    ({**GOOD, "model": None}, "model"),
    ({**GOOD, "model": "jev-1.14.0"}, "版"),
])
def test_jev_broken_answers_are_errors_with_usage(body: object, reason: str) -> None:
    with pytest.raises(InvalidOutputError, match=reason) as e:
        _jev(_reply(body)).decide(_request(("kind", ["a", "b", "c"])))
    assert e.value.usage == Usage(400, 30)  # 課金はされている


@pytest.mark.parametrize("status", [400, 401, 422, 429, 529])
def test_jev_http_failures_are_errors_without_retry(status: int) -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(status, json={"error": "nope"})

    with pytest.raises(LlmError, match=f"HTTP {status}"):
        _jev(handler).decide(_request(("kind", ["a", "b"])))
    assert calls == [1]


def test_jev_connection_failure_keeps_cause() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    with pytest.raises(LlmError) as e:
        _jev(handler).decide(_request(("kind", ["a", "b"])))
    assert isinstance(e.value.cause, httpx2.ConnectError)


def test_jev_answer_without_usage_is_an_error() -> None:
    with pytest.raises(LlmError, match="使用量"):
        _jev(_reply({**GOOD, "usage": None})).decide(_request(("kind", ["a", "b", "c"])))


def test_jev_does_not_send_too_many_options() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(200, json=GOOD)

    with pytest.raises(LlmError, match="上限"):
        _jev(handler).decide(_request(("kind", [f"o{i}" for i in range(MAX_OPTIONS + 1)])))
    assert calls == []


def test_jev_estimate_covers_measured_usage() -> None:
    """実測（2026-10-16）の入力トークン数を、見積もりが上回る。"""
    jev = _jev(_reply(GOOD))
    small = DecisionRequest("t", {"s": "x"}, {"a": Choice("y", {"yes": None, "no": None}),
                                             "b": Choice("z", {"yes": None, "no": None})})
    assert jev.max_input_tokens(small) >= 276


def test_empty_api_key_is_an_error() -> None:
    with pytest.raises(LlmError, match="TYPESAFE_API_KEY"):
        JevModel.from_api_key("jev-1.13.0", " ")


# ---------------------------------------------------------------- 予算の共有


@dataclass
class FakeDecider:
    input_tokens: int
    estimate: int = 1000
    model: str = "jev"
    sent: int = 0
    max_options: int = 255

    def max_input_tokens(self, request: DecisionRequest) -> int:
        return self.estimate

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.sent += 1
        answers = {k: ChoiceAnswer(next(iter(q.options)), {o: 1.0 if i == 0 else 0.0 for i, o in enumerate(q.options)})
                   for k, q in request.questions.items()}
        return DecisionResponse(answers, Usage(self.input_tokens, 20), self.model, None)


@dataclass
class FakeLlm:
    model: str = "llm"
    sent: int = 0
    calls: list[str] = field(default_factory=lambda: list[str]())

    def count_input_tokens(self, request: StructuredRequest) -> int:
        return 1000

    def ask(self, request: StructuredRequest) -> StructuredResponse:
        self.sent += 1
        return StructuredResponse({}, Usage(1000, 100), self.model, None)


@given(st.lists(st.sampled_from(["llm", "decision"]), max_size=30), st.floats(0.0005, 0.02))
def test_shared_budget_holds_the_total(plan: list[str], limit: float) -> None:
    budget = Budget(limit)
    llm = BudgetedModel(FakeLlm(), Pricing("llm", (PriceTier(None, 1.0, 1.0),)), budget)
    decider = BudgetedDecisionModel(FakeDecider(800), Pricing("jev", (PriceTier(None, 1.0, 0.0),)), budget)
    for step in plan:
        try:
            if step == "llm":
                llm.ask(StructuredRequest("q", "s", "p", {"type": "object"}, 100))
            else:
                decider.decide(_request(("k", ["a", "b"])))
        except BudgetExceededError:
            continue
        assert budget.spent_usd <= limit + 1e-12
    assert {s.model for s in budget.spends} <= {"llm", "jev"}


def test_decision_estimate_violation_stops_both() -> None:
    budget = Budget(1.0)
    llm_inner = FakeLlm()
    llm = BudgetedModel(llm_inner, Pricing("llm", (PriceTier(None, 1.0, 1.0),)), budget)
    decider = BudgetedDecisionModel(FakeDecider(input_tokens=5000, estimate=10),
                                    Pricing("jev", (PriceTier(None, 1.0, 0.0),)), budget)
    with pytest.raises(EstimateViolatedError):
        decider.decide(_request(("k", ["a", "b"])))
    with pytest.raises(EstimateViolatedError, match="もう送らない"):
        llm.ask(StructuredRequest("q", "s", "p", {"type": "object"}, 100))
    assert llm_inner.sent == 0


def test_budgeted_decider_refuses_too_many_options_without_spending() -> None:
    budget = Budget(1.0)
    inner = FakeDecider(10, max_options=3)
    decider = BudgetedDecisionModel(inner, Pricing("jev", (PriceTier(None, 1.0, 0.0),)), budget)
    with pytest.raises(ValueError, match="上限"):
        decider.decide(_request(("k", ["a", "b", "c", "d"])))
    assert budget.spends == [] and inner.sent == 0
