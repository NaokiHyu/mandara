"""LLM の単価表（config/llm_prices.yaml）を読む。

Adapter 層（設定の読み込み）。表に無いモデルはエラーにする（推測の単価で予算を守ったことにしない）。
"""

from pathlib import Path
from typing import Any, cast

import yaml

from mandara.llm.model import PriceTier, Pricing

DEFAULT_PATH = Path(__file__).parent.parent.parent.parent / "config/llm_prices.yaml"


class UnknownPricingError(Exception):
    """単価表にそのモデルが無い、または表の形が不正。"""


def load_pricing(model: str, path: Path = DEFAULT_PATH) -> Pricing:
    try:
        data = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
        tiers_raw = cast(dict[str, list[dict[str, Any]]], data["models"]).get(model)
    except (OSError, yaml.YAMLError, KeyError, TypeError) as e:
        raise UnknownPricingError(f"単価表 {path} を読めない: {e}") from e
    if tiers_raw is None:
        raise UnknownPricingError(f"単価表 {path} に {model} が無い。公式の料金表で確かめて書き足す")
    try:
        tiers = tuple(PriceTier(t["max_input_tokens"], float(t["input_per_mtok"]), float(t["output_per_mtok"]))
                      for t in tiers_raw)
    except (KeyError, TypeError, ValueError) as e:
        raise UnknownPricingError(f"単価表の {model} の形が不正: {e}") from e
    limits = [t.max_input_tokens for t in tiers]
    if limits[-1] is not None or sorted(x for x in limits[:-1] if x is not None) != limits[:-1]:
        raise UnknownPricingError(f"単価表の {model} の段は、上限の小さい順で、最後は上限なし（null）")
    return Pricing(model, tiers)
