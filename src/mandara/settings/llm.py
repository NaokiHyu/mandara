"""LLM の設定（.env と環境変数）。

Adapter 層。API キーなどの秘密は .env（git に乗せない）か環境変数に置く。足りない・不正な値はエラーにする
（既定の為替や予算を推測で持たない）。予算は円で受け取り、為替でドルにする。
"""

from collections.abc import Mapping
from dataclasses import dataclass

REQUIRED = ("ANTHROPIC_API_KEY", "MANDARA_LLM_MODEL", "TYPESAFE_API_KEY", "MANDARA_DECISION_MODEL",
            "MANDARA_BUDGET_JPY", "MANDARA_JPY_PER_USD")


class SettingsError(Exception):
    """LLM の設定が足りない、または不正。"""


@dataclass(frozen=True)
class LlmSettings:
    api_key: str
    model: str
    decision_api_key: str
    decision_model: str
    """判断モデル（分類の問い。T3.7）。版を固定する（別名だと閾値を合わせた版から黙って変わる）。"""
    budget_jpy: float
    jpy_per_usd: float

    @property
    def budget_usd(self) -> float:
        return self.budget_jpy / self.jpy_per_usd

    def __repr__(self) -> str:  # API キーを表示しない
        return (f"LlmSettings(model={self.model!r}, decision_model={self.decision_model!r}, "
                f"budget_jpy={self.budget_jpy}, jpy_per_usd={self.jpy_per_usd})")


def parse_settings(env: Mapping[str, str | None]) -> LlmSettings:
    missing = [k for k in REQUIRED if not (env.get(k) or "").strip()]
    if missing:
        raise SettingsError(f"LLM の設定が足りない: {', '.join(missing)}（.env に書く。例は .env.example）")

    def positive(name: str) -> float:
        raw = (env.get(name) or "").strip()
        try:
            value = float(raw)
        except ValueError as e:
            raise SettingsError(f"{name} が数ではない: {raw!r}") from e
        if not value > 0:
            raise SettingsError(f"{name} は正の数: {raw!r}")
        return value

    return LlmSettings(
        api_key=(env.get("ANTHROPIC_API_KEY") or "").strip(),
        model=(env.get("MANDARA_LLM_MODEL") or "").strip(),
        decision_api_key=(env.get("TYPESAFE_API_KEY") or "").strip(),
        decision_model=(env.get("MANDARA_DECISION_MODEL") or "").strip(),
        budget_jpy=positive("MANDARA_BUDGET_JPY"),
        jpy_per_usd=positive("MANDARA_JPY_PER_USD"),
    )
