"""ソースの宣言（work/<source>/declarations.yaml）の読み書き。

Adapter 層。宣言は人が書く（mandara declare）。読む境界で形を検証する（手で編集されうる。読めなければ OmLoadError）。
ファイルが無いことは「何も宣言していない」で、空の宣言として返す（宣言の無いソースは普通にある。黙った代用ではない）。
"""

from pathlib import Path

from mandara.om.model import SourceDeclarations
from mandara.om.value_types import CURRENCIES
from mandara.omstore import codec

FILE = "declarations.yaml"


class InvalidDeclarationError(Exception):
    """宣言の値が不正（通貨でない単位を通貨に宣言した、など）。"""


def check_declarations(declarations: SourceDeclarations) -> None:
    if declarations.default_currency is not None and str(declarations.default_currency) not in CURRENCIES:
        raise InvalidDeclarationError(f"既定の通貨に通貨でない単位: {declarations.default_currency}（通貨は {sorted(CURRENCIES)}）")


def load_declarations(root: Path) -> SourceDeclarations:
    path = root / FILE
    if not path.exists():
        return SourceDeclarations()
    declarations = codec.load(path, SourceDeclarations)
    check_declarations(declarations)
    return declarations


def write_declarations(root: Path, declarations: SourceDeclarations) -> Path:
    check_declarations(declarations)
    root.mkdir(parents=True, exist_ok=True)
    path = root / FILE
    path.write_text(codec.dump(declarations), encoding="utf-8")
    return path
