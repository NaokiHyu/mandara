"""LLM の層の法則。本物の API は呼ばない（呼ぶのは mandara llm-check だけ）。

- 予算：どんな問いの並びでも、使った費用の累計は上限を超えない。超えるおそれのある問いは送らない
- 見積もりの前提が崩れたら（実際の費用 > 見積もり）、黙って続けずに止める
- 単価：入力の量で段が変わる。表に無いモデル・壊れた表はエラー
- Claude の実装：答えの道具で答えさせ、途中で切れた・道具を使わなかった・SDK の失敗はエラー（原因を保持）
- 設定：足りない・不正な値はエラー。API キーを表示しない
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.llm.claude.model import ClaudeModel
from mandara.llm.model import (
    ESTIMATE_MARGIN,
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
from mandara.settings.llm import SettingsError, parse_settings
from mandara.settings.llm_prices import UnknownPricingError, load_pricing

PRICING = Pricing("m", (PriceTier(100_000, 0.10, 0.50), PriceTier(None, 0.50, 2.50)))


def _request(max_output: int = 100, purpose: str = "t") -> StructuredRequest:
    return StructuredRequest(purpose, "sys", "prompt", {"type": "object"}, max_output)


@dataclass(frozen=True)
class Step:
    counted: int
    """数える入力トークン。"""
    actual_in: int
    actual_out: int
    max_output: int
    outcome: str
    """answered / invalid / failed / cache"""


@dataclass
class FakeModel:
    """計画どおりに数え、答え、失敗する。"""

    plan: list[Step]
    model: str = "m"
    sent: list[int] = field(default_factory=lambda: list[int]())
    asked: int = 0

    def count_input_tokens(self, request: StructuredRequest) -> int:
        return self.plan[self.asked].counted

    def ask(self, request: StructuredRequest) -> StructuredResponse:
        step = self.plan[self.asked]
        self.sent.append(self.asked)
        self.asked += 1
        usage = Usage(step.actual_in, step.actual_out, 5 if step.outcome == "cache" else 0)
        if step.outcome == "invalid":
            raise InvalidOutputError("壊れた答え", usage)
        if step.outcome == "failed":
            raise LlmError("接続が切れた")
        return StructuredResponse({}, usage, self.model, None)


@st.composite
def plans(draw: st.DrawFn) -> list[Step]:
    out: list[Step] = []
    for _ in range(draw(st.integers(1, 30))):
        counted = draw(st.integers(1, 120_000))
        max_output = draw(st.integers(1, 4000))
        honest = draw(st.integers(0, 9)) > 0  # たいていは見積もりの前提（入力 ≤ 数えた×余裕）を守る
        limit_in = math.ceil(counted * ESTIMATE_MARGIN)
        actual_in = draw(st.integers(1, limit_in) if honest else st.integers(limit_in + 1, limit_in + 50_000))
        out.append(Step(counted, actual_in, draw(st.integers(0, max_output)), max_output,
                        draw(st.sampled_from(["answered"] * 6 + ["invalid", "failed", "cache"]))))
    return out


@given(plans(), st.floats(0.0001, 0.2))
def test_budget_laws(plan: list[Step], limit: float) -> None:
    """前提が崩れない限り累計は上限以下。課金されうる費用はすべて累計。崩れたら、それ以後は何も送らない。"""
    model = FakeModel(plan)
    budgeted = BudgetedModel(model, PRICING, Budget(limit))
    budget = budgeted.budget
    for n, step in enumerate(plan):
        sent_before, spent_before = len(model.sent), budget.spent_usd
        was_violated = budget.violated is not None
        model.asked = n
        try:
            budgeted.ask(_request(step.max_output, f"q{n}"))
        except BudgetExceededError:
            assert len(model.sent) == sent_before and budget.spent_usd == spent_before  # 送っていない
            continue
        except EstimateViolatedError:
            if was_violated:
                assert len(model.sent) == sent_before  # 崩れた後は送らない
                continue
        except LlmError:
            pass
        if was_violated:
            raise AssertionError("前提が崩れた後に送った")
        # 送った。課金されうる費用が累計に入っている
        assert len(budget.spends) == len(model.sent)
        last = budget.spends[-1]
        if step.outcome == "failed":
            assert last.usage is None and last.cost_usd > 0  # 分からない費用は最悪の見積もりで
        elif step.outcome != "cache":
            assert last.cost_usd == pytest.approx(PRICING.cost_usd(step.actual_in, step.actual_out))
        if budget.violated is None:
            assert budget.spent_usd <= limit + 1e-12


def test_estimate_violation_stops_everything_after() -> None:
    step = Step(10, 10_000, 10, 100, "answered")  # 数えた 10 より実際の入力がずっと多い
    model = FakeModel([step, step])
    budgeted = BudgetedModel(model, PRICING, Budget(1.0))
    with pytest.raises(EstimateViolatedError):
        budgeted.ask(_request())
    assert len(budgeted.budget.spends) == 1  # 使った費用は記録する
    with pytest.raises(EstimateViolatedError, match="もう送らない"):
        budgeted.ask(_request())
    assert len(model.sent) == 1


def test_tier_boundary_is_estimated_pessimistically() -> None:
    """数えた入力が段の境目の少し下でも、余裕を掛けた数が境目を超えるなら高い段で見積もる。"""
    budgeted = BudgetedModel(FakeModel([Step(95_000, 95_000, 0, 10, "answered")]), PRICING, Budget(1.0))
    request = _request(10)
    assert budgeted.worst_case_usd(request) == pytest.approx(PRICING.cost_usd(math.ceil(95_000 * ESTIMATE_MARGIN), 10))
    assert budgeted.worst_case_usd(request) > PRICING.cost_usd(95_000, 10) * 4  # 高い段（5 倍）


def test_budget_needs_matching_pricing_and_positive_limit() -> None:
    with pytest.raises(ValueError, match="違う"):
        BudgetedModel(FakeModel([], model="other"), PRICING, Budget(1.0))
    with pytest.raises(ValueError, match="正の値"):
        Budget(0)


def test_pricing_tiers() -> None:
    assert PRICING.cost_usd(100_000, 1_000_000) == pytest.approx(0.01 + 0.5)
    assert PRICING.cost_usd(100_001, 0) == pytest.approx(100_001 * 0.50 / 1_000_000)


def test_load_pricing_from_repository_table() -> None:
    haiku = load_pricing("claude-haiku-5-5")
    assert haiku.cost_usd(1_000_000 // 10, 0) == pytest.approx(0.01)
    with pytest.raises(UnknownPricingError, match="無い"):
        load_pricing("claude-unknown")


def test_broken_pricing_table_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "prices.yaml"
    path.write_text("models:\n  m:\n    - {max_input_tokens: 10, input_per_mtok: 1, output_per_mtok: 1}\n")
    with pytest.raises(UnknownPricingError, match="上限なし"):
        load_pricing("m", path)


# ---------------------------------------------------------------- Claude の実装


@dataclass
class FakeMessages:
    reply: Any
    calls: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def count_tokens(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(input_tokens=42)


@dataclass
class FakeClient:
    messages: FakeMessages


def _message(content: list[Any], stop_reason: str = "tool_use") -> Any:
    return SimpleNamespace(content=content, stop_reason=stop_reason, model="claude-haiku-5-5",
                           usage=SimpleNamespace(input_tokens=40, output_tokens=7))


def _tool(data: object, name: str = "answer") -> Any:
    return SimpleNamespace(type="tool_use", name=name, input=data)


def test_claude_answers_through_the_tool() -> None:
    messages = FakeMessages(_message([_tool({"ok": True})]))
    model = ClaudeModel("claude-haiku-5-5", FakeClient(messages))
    assert model.count_input_tokens(_request()) == 42
    response = model.ask(_request())
    assert (response.data, response.usage) == ({"ok": True}, Usage(40, 7))
    counted, sent = messages.calls[0], messages.calls[-1]
    assert sent["tool_choice"] == {"type": "tool", "name": "answer"}
    # 数えた問いと送った問いが同じ（違うのは出力の上限だけ）
    assert {k: v for k, v in sent.items() if k != "max_tokens"} == counted


@pytest.mark.parametrize(("reply", "reason"), [
    (_message([_tool({"ok": True})], stop_reason="max_tokens"), "切れた"),
    (_message([SimpleNamespace(type="text", text="ok")]), "ちょうど1回"),
    (_message([_tool({"a": 1}), _tool({"b": 2})]), "ちょうど1回"),
    (_message([_tool(["not", "object"])]), "オブジェクトではない"),
])
def test_invalid_output_is_an_error(reply: Any, reason: str) -> None:
    with pytest.raises(InvalidOutputError, match=reason):
        ClaudeModel("claude-haiku-5-5", FakeClient(FakeMessages(reply))).ask(_request())


def test_invalid_output_carries_usage() -> None:
    reply = _message([_tool({"ok": True})], stop_reason="max_tokens")
    with pytest.raises(InvalidOutputError) as e:
        ClaudeModel("claude-haiku-5-5", FakeClient(FakeMessages(reply))).ask(_request())
    assert e.value.usage == Usage(40, 7)  # 課金された分を、予算の側が数えられる


def test_cache_tokens_are_reported() -> None:
    reply = _message([_tool({"ok": True})])
    reply.usage.cache_read_input_tokens = 30
    response = ClaudeModel("claude-haiku-5-5", FakeClient(FakeMessages(reply))).ask(_request())
    assert response.usage.cache_tokens == 30


def test_client_does_not_retry_silently() -> None:
    model = ClaudeModel.from_api_key("claude-haiku-5-5", "sk-secret")
    assert getattr(model._client, "max_retries") == 0  # pyright: ignore[reportPrivateUsage]


def test_sdk_failure_keeps_cause() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(LlmError) as e:
        ClaudeModel("claude-haiku-5-5", FakeClient(FakeMessages(error))).ask(_request())
    assert e.value.cause is error

    class FailingCount(FakeMessages):
        def count_tokens(self, **kwargs: Any) -> Any:
            raise error

    with pytest.raises(LlmError) as counted:
        ClaudeModel("claude-haiku-5-5", FakeClient(FailingCount(None))).count_input_tokens(_request())
    assert counted.value.cause is error


# ---------------------------------------------------------------- 設定

GOOD = {"ANTHROPIC_API_KEY": "sk-secret", "MANDARA_LLM_MODEL": "claude-haiku-5-5",
        "TYPESAFE_API_KEY": "ts-secret", "MANDARA_DECISION_MODEL": "jev-1.13.0",
        "MANDARA_BUDGET_JPY": "10", "MANDARA_JPY_PER_USD": "150"}


def test_settings() -> None:
    settings = parse_settings(GOOD)
    assert settings.budget_usd == pytest.approx(10 / 150)
    for secret in ("sk-secret", "ts-secret"):
        assert secret not in repr(settings) and secret not in str(settings)
    assert settings.decision_model == "jev-1.13.0"
    with pytest.raises(SettingsError) as e:
        parse_settings({**GOOD, "MANDARA_BUDGET_JPY": "x"})
    assert "sk-secret" not in str(e.value)


@pytest.mark.parametrize(("change", "reason"), [
    ({"ANTHROPIC_API_KEY": ""}, "足りない"),
    ({"TYPESAFE_API_KEY": " "}, "足りない"),
    ({"MANDARA_DECISION_MODEL": None}, "足りない"),
    ({"MANDARA_JPY_PER_USD": None}, "足りない"),
    ({"MANDARA_BUDGET_JPY": "ten"}, "数ではない"),
    ({"MANDARA_BUDGET_JPY": "-1"}, "正の数"),
])
def test_bad_settings_are_errors(change: dict[str, str | None], reason: str) -> None:
    with pytest.raises(SettingsError, match=reason):
        parse_settings({**GOOD, **change})
