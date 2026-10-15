"""Claude（Anthropic API）による LanguageModel の実装。

Adapter 層。SDK の型・例外はこのファイルの外に出さない。
- 構造化された答えは、答え専用の道具（answer）を1つだけ渡して使わせる（tool_choice で強制）。道具の入力が答え
- 途中で切れた（max_tokens）、道具を使わなかった、は InvalidOutputError。黙ってリトライしない
- SDK の例外は LlmError に変換し、原因を保持する
- 温度は指定しない（この SDK の版の Messages.create は temperature を受け付けない）。同じ問いに同じ答えが返る保証は無いので、
  答えは実行の記録に残す
"""

from collections.abc import Mapping
from typing import Any, Protocol, cast

import anthropic

from mandara.llm.model import InvalidOutputError, LlmError, StructuredRequest, StructuredResponse, Usage

_TOOL = "answer"


class _Messages(Protocol):
    def create(self, **kwargs: Any) -> Any: ...

    def count_tokens(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    @property
    def messages(self) -> _Messages: ...


class ClaudeModel:
    def __init__(self, model: str, client: _Client) -> None:
        """client は anthropic.Anthropic（テストでは同じ形の偽物）。API キーは client を作る側が渡す。"""
        self._model = model
        self._client = client

    @classmethod
    def from_api_key(cls, model: str, api_key: str) -> "ClaudeModel":
        if not api_key.strip():
            raise LlmError("ANTHROPIC_API_KEY が空")
        return cls(model, cast(_Client, anthropic.Anthropic(api_key=api_key, max_retries=0)))

    @property
    def model(self) -> str:
        return self._model

    def _arguments(self, request: StructuredRequest) -> dict[str, Any]:
        return {
            "model": self._model,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            "tools": [{"name": _TOOL, "description": "問いへの答え。必ずこの道具で、決めた形で答える",
                       "input_schema": dict(request.schema)}],
            "tool_choice": {"type": "tool", "name": _TOOL},
        }

    def count_input_tokens(self, request: StructuredRequest) -> int:
        try:
            counted = self._client.messages.count_tokens(**self._arguments(request))
        except anthropic.APIError as e:
            raise LlmError(f"入力のトークン数を数えられなかった（{request.purpose}）: {e}", e) from e
        return int(counted.input_tokens)

    def ask(self, request: StructuredRequest) -> StructuredResponse:
        try:
            message = self._client.messages.create(
                **self._arguments(request), max_tokens=request.max_output_tokens)
        except anthropic.APIError as e:
            raise LlmError(f"Claude を呼べなかった（{request.purpose}）: {e}", e) from e
        u = message.usage
        cache = sum(int(getattr(u, name, 0) or 0) for name in ("cache_creation_input_tokens", "cache_read_input_tokens"))
        usage = Usage(int(u.input_tokens), int(u.output_tokens), cache)
        request_id = cast(str | None, getattr(message, "_request_id", None))
        if message.stop_reason == "max_tokens":
            raise InvalidOutputError(f"答えが出力の上限 {request.max_output_tokens} で切れた（{request.purpose}）",
                                     usage, request_id)
        answers = [block for block in message.content if getattr(block, "type", None) == "tool_use"
                   and getattr(block, "name", None) == _TOOL]
        if len(answers) != 1:
            raise InvalidOutputError(f"答えの道具をちょうど1回使っていない（{len(answers)} 回、{request.purpose}）",
                                     usage, request_id)
        data = answers[0].input
        if not isinstance(data, Mapping):
            raise InvalidOutputError(f"答えがオブジェクトではない（{request.purpose}）", usage, request_id)
        return StructuredResponse(data=dict(cast(Mapping[str, object], data)), usage=usage, model=str(message.model),
                                  request_id=request_id)
