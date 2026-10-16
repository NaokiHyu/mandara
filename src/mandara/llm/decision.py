"""判断モデルのポート（T3.7）。選択肢を決めて問い、選択肢ごとの確率を受け取る。

Domain 層。実装（Jev など）の型を知らない。
- 文を生成させない。名前や文が要る問いは LanguageModel（mandara.llm.model）に問う
- 答えは信頼できない入力。選択肢の外の値、確率の欠け・和の崩れは実装の境界で InvalidOutputError にする
- 失敗は LlmError（とその下位）。黙って再送しない
- 予算は LLM と共有する（Budget）。送る前に最悪の費用を見積もり、超えるなら送らない
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mandara.llm.model import (
    ESTIMATE_MARGIN,
    Budget,
    InvalidOutputError,
    LlmError,
    Pricing,
    Usage,
)

Text = str | Mapping[str, object]
"""問いの文。文字列か、問いの文と、それが指すデータを分けたオブジェクト。"""


@dataclass(frozen=True)
class Choice:
    """選択肢から1つ選ばせる問い。"""

    instructions: Text
    options: Mapping[str, str | None]
    """選択肢 → その説明（None は説明なし）。並びも問いの一部。"""

    def __post_init__(self) -> None:
        if len(self.options) < 2:
            raise ValueError(f"選択肢は 2 つ以上: {list(self.options)}")


@dataclass(frozen=True)
class DecisionRequest:
    purpose: str
    """何のための問いか（実行の記録に残す）。"""
    state: Mapping[str, object]
    """判断の材料。すべての問いが同じ材料を見る。"""
    questions: Mapping[str, Choice]
    """問いの ID → 問い。答えは同じ ID で返る。"""

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError(f"問いが無い（{self.purpose}）")


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: Mapping[str, float]
    """すべての選択肢 → 確率。"""

    @property
    def probability(self) -> float:
        """選んだ選択肢の確率（提案の確信度に使う）。"""
        return self.probabilities[self.choice]


@dataclass(frozen=True)
class DecisionResponse:
    answers: Mapping[str, ChoiceAnswer]
    usage: Usage
    model: str
    """答えたモデルの版（別名で問うても、実際の版）。"""
    request_id: str | None


class DecisionModel(Protocol):
    """判断モデルのポート。失敗は LlmError（とその下位）。"""

    @property
    def model(self) -> str: ...

    @property
    def max_options(self) -> int:
        """1 つの問いの選択肢の上限（モデルの能力。問いを作る側はこれを超えない）。"""
        ...

    def max_input_tokens(self, request: DecisionRequest) -> int:
        """入力トークン数の上限の見積もり（実際がこれを超えたら、見積もりの前提が崩れたとして止める）。"""
        ...

    def decide(self, request: DecisionRequest) -> DecisionResponse: ...


def check_answers(request: DecisionRequest, answers: Mapping[str, ChoiceAnswer], usage: Usage,
                  request_id: str | None, sum_tolerance: float) -> None:
    """答えが問いに合っているか（実装の境界で使う）。合っていなければ InvalidOutputError。

    sum_tolerance：確率の和の 1 からのずれの許容（実装の丸めによる。実装が知っている値を渡す）。"""
    if set(answers) != set(request.questions):
        raise InvalidOutputError(f"答えの ID が問いと合わない（{request.purpose}）: 欠け "
                                 f"{sorted(set(request.questions) - set(answers))[:3]}、余り "
                                 f"{sorted(set(answers) - set(request.questions))[:3]}", usage, request_id)
    for key, answer in answers.items():
        options = request.questions[key].options
        if set(answer.probabilities) != set(options) or answer.choice not in options:
            raise InvalidOutputError(f"選択肢の外の答え（{request.purpose} {key}）", usage, request_id)
        values = list(answer.probabilities.values())
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in values):
            raise InvalidOutputError(f"確率が 0〜1 ではない（{request.purpose} {key}）", usage, request_id)
        if abs(sum(values) - 1) > sum_tolerance:
            raise InvalidOutputError(f"確率の和が 1 ではない（{request.purpose} {key}: {sum(values):.3f}）",
                                     usage, request_id)


@dataclass
class BudgetedDecisionModel:
    """予算の上限を守る DecisionModel。予算は LLM と共有する。"""

    inner: DecisionModel
    pricing: Pricing
    budget: Budget

    def __post_init__(self) -> None:
        if self.pricing.model != self.inner.model:
            raise ValueError(f"単価のモデル {self.pricing.model} と、使うモデル {self.inner.model} が違う")

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def max_options(self) -> int:
        return self.inner.max_options

    def max_input_tokens(self, request: DecisionRequest) -> int:
        return self.inner.max_input_tokens(request)

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        if over := [k for k, q in request.questions.items() if len(q.options) > self.inner.max_options]:
            # 問いを作る側の誤り。送っていないので予算には記録しない
            raise ValueError(f"選択肢が上限 {self.inner.max_options} を超える問い（{request.purpose}）: {over[:3]}")
        worst = self.pricing.cost_usd(math.ceil(self.inner.max_input_tokens(request) * ESTIMATE_MARGIN), 0)
        self.budget.admit(request.purpose, worst)
        try:
            response = self.inner.decide(request)
        except InvalidOutputError as e:
            self.budget.record(request.purpose, self.pricing, e.usage, e.request_id, "invalid output", worst)
            raise
        except LlmError:
            self.budget.failed(request.purpose, self.model, worst)
            raise
        self.budget.record(request.purpose, self.pricing, response.usage, response.request_id, "answered", worst)
        return response
