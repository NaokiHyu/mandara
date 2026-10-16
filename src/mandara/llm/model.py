"""LLM のポートと予算（仕様 6.3、CLAUDE.md「LLM は外部 I/O のひとつ」）。

Domain 層。SDK の型を知らない。
- 問いは「決めた JSON の形で答えさせる」構造化された問いだけ。自由記述の答えは受け取らない
- 答えは信頼できない入力。形の検証は、受け取る側（使うユースケース）が行う。失敗したらエラーで、黙ってリトライしない
- 予算（Budget。LLM と判断モデル〔mandara.llm.decision〕で共有し、合計で上限を守る）：送る前に最悪の費用（余裕を掛けた入力トークン数 × 入力の単価 ＋ 出力の上限 × 出力の単価）を見積もり、
  累計が上限を超えるなら送らずに BudgetExceededError。送った後は、答えの成否にかかわらず課金されうる費用を累計する。
  見積もりの前提が崩れたら以後は送らない。安いモデルへの切り替えはしない
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class StructuredRequest:
    purpose: str
    """何のための問いか（実行の記録に残す。例 "table-kind sales.orders"）。"""
    system: str
    prompt: str
    schema: Mapping[str, object]
    """答えの JSON Schema（オブジェクト）。"""
    max_output_tokens: int


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_tokens: int = 0
    """キャッシュの書き込み・読み出しのトークン（使っていない。0 でなければ単価が無いので止める）。"""


@dataclass(frozen=True)
class StructuredResponse:
    data: object
    """答えの JSON（形はまだ検証していない）。"""
    usage: Usage
    model: str
    request_id: str | None


class LanguageModel(Protocol):
    """LLM のポート。失敗は LlmError（とその下位）。"""

    @property
    def model(self) -> str: ...

    def count_input_tokens(self, request: StructuredRequest) -> int: ...

    def ask(self, request: StructuredRequest) -> StructuredResponse: ...


class LlmError(Exception):
    """LLM を呼べなかった、または答えを受け取れなかった。原因を cause に保持する。"""

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class InvalidOutputError(LlmError):
    """答えが決めた形で返ってこなかった（途中で切れた、答えの道具を使わなかった、など）。送って課金はされている。"""

    def __init__(self, message: str, usage: Usage, request_id: str | None = None) -> None:
        super().__init__(message)
        self.usage = usage
        self.request_id = request_id


# ---------------------------------------------------------------- 単価と予算


@dataclass(frozen=True)
class PriceTier:
    max_input_tokens: int | None
    """この段の入力の上限（1 回の問いの入力トークン数）。None は上限なし。"""
    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class Pricing:
    """モデルの単価（USD / 100 万トークン）。入力の量で単価の段が変わるモデルがある。"""

    model: str
    tiers: tuple[PriceTier, ...]
    """max_input_tokens の小さい順。"""

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        tier = next((t for t in self.tiers if t.max_input_tokens is None or input_tokens <= t.max_input_tokens), None)
        if tier is None:
            raise ValueError(f"{self.model} の単価に、入力 {input_tokens} トークンの段が無い")
        return (input_tokens * tier.input_per_mtok + output_tokens * tier.output_per_mtok) / 1_000_000


class BudgetExceededError(LlmError):
    """この問いを送ると、予算の上限を超えるおそれがある。送っていない。"""

    def __init__(self, purpose: str, spent_usd: float, worst_case_usd: float, limit_usd: float) -> None:
        super().__init__(
            f"予算の上限を超えるおそれがあるので送らない（{purpose}）：これまで ${spent_usd:.5f}、"
            f"この問いの最悪 ${worst_case_usd:.5f}、上限 ${limit_usd:.5f}")
        self.spent_usd = spent_usd
        self.worst_case_usd = worst_case_usd
        self.limit_usd = limit_usd


@dataclass(frozen=True)
class Spend:
    purpose: str
    usage: Usage | None
    """None は使用量が分からない（送った後に失敗した）。そのときは cost_usd に最悪の見積もりを入れる。"""
    cost_usd: float
    request_id: str | None
    outcome: str
    """"answered" / "invalid output"（課金済み、答えは不正）/ "failed"（送った後に失敗、費用は最悪の見積もり）。"""
    model: str


ESTIMATE_MARGIN = 1.1
"""見積もりで、数えた入力トークン数に掛ける余裕。数えた数と請求される数がずれても上限を超えないように。
費用ではなくトークン数に掛けるので、単価の段の選び方も悲観側に寄る（境目をまたいでも高い段で見積もる）。"""


class EstimateViolatedError(LlmError):
    """実際の費用が送る前の最悪の見積もりを超えた、または単価の無い課金（キャッシュ）があった。見積もりの前提が崩れたので、
    このオブジェクトはもう送らない。"""


@dataclass
class Budget:
    """1回の実行（このオブジェクトの寿命）の予算。LLM と判断モデルで共有する（上限は合計で守る）。

    課金されうる費用は、答えの成否にかかわらず累計する（不正な答えは実際の使用量、送った後の失敗は最悪の見積もり）。
    """

    limit_usd: float
    spends: list[Spend] = field(default_factory=lambda: list[Spend]())
    violated: str | None = None
    """見積もりの前提が崩れた理由。崩れたら以後は何も送らない。"""

    def __post_init__(self) -> None:
        if self.limit_usd <= 0:
            raise ValueError(f"予算の上限は正の値: {self.limit_usd}")

    @property
    def spent_usd(self) -> float:
        return sum(s.cost_usd for s in self.spends)

    def admit(self, purpose: str, worst: float) -> None:
        """送ってよいか。だめなら例外（送っていない）。"""
        if self.violated is not None:
            raise EstimateViolatedError(f"見積もりの前提が崩れたので、もう送らない: {self.violated}")
        if self.spent_usd + worst > self.limit_usd:
            raise BudgetExceededError(purpose, self.spent_usd, worst, self.limit_usd)

    def failed(self, purpose: str, model: str, worst: float) -> None:
        """送った後の失敗（接続断など）。課金されたかどうか分からないので、最悪の見積もりを使ったことにする。"""
        self.spends.append(Spend(purpose, None, worst, None, "failed", model))

    def record(self, purpose: str, pricing: Pricing, usage: Usage, request_id: str | None, outcome: str,
               worst: float) -> None:
        if usage.cache_tokens:
            self.spends.append(Spend(purpose, usage, worst, request_id, outcome, pricing.model))
            self.violated = f"単価の無いキャッシュの課金がある（{usage.cache_tokens} トークン、{purpose}）"
            raise EstimateViolatedError(self.violated)
        cost = pricing.cost_usd(usage.input_tokens, usage.output_tokens)
        self.spends.append(Spend(purpose, usage, cost, request_id, outcome, pricing.model))
        if cost > worst:
            self.violated = f"実際の費用 ${cost:.6f} が見積もり ${worst:.6f} を超えた（{purpose}）"
            raise EstimateViolatedError(self.violated)


@dataclass
class BudgetedModel:
    """予算の上限を守る LanguageModel。"""

    inner: LanguageModel
    pricing: Pricing
    budget: Budget

    def __post_init__(self) -> None:
        if self.pricing.model != self.inner.model:
            raise ValueError(f"単価のモデル {self.pricing.model} と、使うモデル {self.inner.model} が違う")

    @property
    def model(self) -> str:
        return self.inner.model

    def count_input_tokens(self, request: StructuredRequest) -> int:
        return self.inner.count_input_tokens(request)

    def worst_case_usd(self, request: StructuredRequest) -> float:
        counted = self.inner.count_input_tokens(request)
        return self.pricing.cost_usd(math.ceil(counted * ESTIMATE_MARGIN), request.max_output_tokens)

    def ask(self, request: StructuredRequest) -> StructuredResponse:
        if self.budget.violated is not None:  # 数える呼び出しもしない
            self.budget.admit(request.purpose, 0)
        worst = self.worst_case_usd(request)
        self.budget.admit(request.purpose, worst)
        try:
            response = self.inner.ask(request)
        except InvalidOutputError as e:
            self.budget.record(request.purpose, self.pricing, e.usage, e.request_id, "invalid output", worst)
            raise
        except LlmError:
            self.budget.failed(request.purpose, self.model, worst)
            raise
        self.budget.record(request.purpose, self.pricing, response.usage, response.request_id, "answered", worst)
        return response


def summarize(spends: Sequence[Spend]) -> dict[str, object]:
    """実行の記録に残す費用のまとめ。"""
    return {
        "requests": len(spends),
        "input_tokens": sum(s.usage.input_tokens for s in spends if s.usage is not None),
        "output_tokens": sum(s.usage.output_tokens for s in spends if s.usage is not None),
        "unknown_usage": sum(1 for s in spends if s.usage is None),
        "cost_usd": round(sum(s.cost_usd for s in spends), 6),
    }
