"""TypeSafe の Jev による DecisionModel の実装（T3.7）。

Adapter 層。HTTP API（POST /v1/systemone）を直接呼ぶ。HTTP クライアントの型・例外はこのファイルの外に出さない。
- SDK は使わない：既定で黙って再送し（429 / 529）、本文を圧縮する。どちらも要らない
- 再送しない。HTTP の失敗（429 / 529 を含む）は LlmError（原因と本文の先頭を保持）
- 答えの形（型、選択肢、確率）は境界で検証し、合わなければ InvalidOutputError（課金はされている）
- 選択肢の上限（1 問 255）を超える問いは送らずに LlmError
"""

import json
import math
from collections.abc import Mapping
from typing import Any, cast

import httpx2

from mandara.llm.decision import ChoiceAnswer, DecisionRequest, DecisionResponse, check_answers
from mandara.llm.model import InvalidOutputError, LlmError, Usage

URL = "https://api.typesafe.ai/v1/systemone"
MAX_OPTIONS = 255
"""1 つの Choice の選択肢の上限（https://docs.typesafe.ai/api 、2026-10-16 に確認）。"""
SUM_TOLERANCE = 0.05
"""確率の和の 1 からのずれの許容。確率は小数 2 桁に丸めて返る。実測（2026-10-16、787 問、選択肢は最大 100 個余り）で
最大 0.01。それより大きく崩れたら、丸めではなく答えの異常とみなす。"""
OVERHEAD_TOKENS = 500
PER_QUESTION_TOKENS = 300
"""入力トークン数の見積もりの上乗せ。実測（2026-10-16）：本文 約 150 バイトの問い 2 つで 276 トークン、
約 650 バイトの問い 1 つで 439 トークン。本文のバイト数の 2 倍に、これを足した値を上限とする。"""


class JevModel:
    def __init__(self, model: str, client: httpx2.Client) -> None:
        """client は API キーの Authorization ヘッダを持つ httpx2.Client（テストでは偽の transport）。"""
        self._model = model
        self._client = client

    @classmethod
    def from_api_key(cls, model: str, api_key: str, timeout: float = 120.0) -> "JevModel":
        if not api_key.strip():
            raise LlmError("TYPESAFE_API_KEY が空")
        return cls(model, httpx2.Client(headers={"Authorization": f"Bearer {api_key.strip()}"}, timeout=timeout))

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_options(self) -> int:
        return MAX_OPTIONS

    def _body(self, request: DecisionRequest) -> dict[str, Any]:
        return {
            "model": self._model,
            "state": dict(request.state),
            "questions": {key: {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
                          for key, q in request.questions.items()},
        }

    def max_input_tokens(self, request: DecisionRequest) -> int:
        size = len(json.dumps(self._body(request), ensure_ascii=False).encode("utf-8"))
        return 2 * size + OVERHEAD_TOKENS + PER_QUESTION_TOKENS * len(request.questions)

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        if over := [k for k, q in request.questions.items() if len(q.options) > MAX_OPTIONS]:
            raise LlmError(f"選択肢が上限 {MAX_OPTIONS} を超える問い（{request.purpose}）: {over[:3]}")
        try:
            response = self._client.post(URL, json=self._body(request))
        except httpx2.HTTPError as e:
            raise LlmError(f"Jev を呼べなかった（{request.purpose}）: {e}", e) from e
        request_id = response.headers.get("x-typesafe-request-id")
        if response.status_code != 200:
            raise LlmError(f"Jev がエラーを返した（{request.purpose}）: HTTP {response.status_code} "
                           f"{response.text[:300]}（request {request_id}）")
        try:
            data = cast(object, response.json())
        except ValueError as e:
            raise LlmError(f"Jev の答えが JSON ではない（{request.purpose}、request {request_id}）", e) from e
        return _parse(request, data, request_id, self._model)


def _parse(request: DecisionRequest, data: object, request_id: str | None, expected_model: str) -> DecisionResponse:
    """答えを検証して読む。使用量が読めない答えは課金が分からないので LlmError（最悪の見積もりで累計される）。"""
    if not isinstance(data, Mapping):
        raise LlmError(f"Jev の答えがオブジェクトではない（{request.purpose}）")
    body = cast(Mapping[str, object], data)
    usage = _usage(body.get("usage"))
    if usage is None:
        raise LlmError(f"Jev の答えに使用量が無い（{request.purpose}、request {request_id}）")
    model = body.get("model")
    raw = body.get("answers")
    if not isinstance(model, str) or not isinstance(raw, Mapping):
        raise InvalidOutputError(f"Jev の答えに model / answers が無い（{request.purpose}）", usage, request_id)
    if model != expected_model:  # 版を固定している（閾値を合わせた版から黙って変わらない）
        raise InvalidOutputError(f"答えたモデルの版 {model} が、問うた版 {expected_model} と違う（{request.purpose}）",
                                 usage, request_id)
    answers: dict[str, ChoiceAnswer] = {}
    for key, value in cast(Mapping[str, object], raw).items():
        answer = _choice(value)
        if answer is None:
            raise InvalidOutputError(f"Choice の答えの形ではない（{request.purpose} {key}）", usage, request_id)
        answers[key] = answer
    check_answers(request, answers, usage, request_id, SUM_TOLERANCE)
    return DecisionResponse(answers, usage, model, request_id)


def _usage(raw: object) -> Usage | None:
    if not isinstance(raw, Mapping):
        return None
    u = cast(Mapping[str, object], raw)
    tokens = (u.get("input_tokens"), u.get("output_tokens"))
    if not all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in tokens):
        return None
    return Usage(cast(int, tokens[0]), cast(int, tokens[1]))


def _choice(raw: object) -> ChoiceAnswer | None:
    if not isinstance(raw, Mapping):
        return None
    a = cast(Mapping[str, object], raw)
    choice, probabilities = a.get("choice"), a.get("probabilities")
    if a.get("type") != "choice" or not isinstance(choice, str) or not isinstance(probabilities, Mapping):
        return None
    out: dict[str, float] = {}
    for option, p in cast(Mapping[object, object], probabilities).items():
        if not isinstance(option, str) or isinstance(p, bool) or not isinstance(p, int | float):
            return None
        if not math.isfinite(p):
            return None
        out[option] = float(p)
    return ChoiceAnswer(choice, out)
