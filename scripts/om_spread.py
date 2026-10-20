"""推論の中で名付けた意味型が、判断モデルの分類で他の表の列に広がった数を数える（T3.10 の created_date の広がり）。

実行の記録（work/<source>/runs の最新）を表の順にたどり、命名の答えに初めて出た名前を「その表で名付けた型」とする。
知識パックの意味型は名付けた型に数えない。正解の表では、広がった型を持つ列の組が正解でも同じ意味型かを数える。
使い方: uv run python scripts/om_spread.py --gold work/gold_bare3 --pack packs/bicycle-wholesale aw_bare4_pack4
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from mandara.omstore.pack_store import load_pack
from mandara.omstore.yaml_store import load_ontology


def _part(value: object, key: str) -> dict[str, Any]:
    """記録の辞書の中の辞書（無ければ空）。"""
    found = cast(dict[str, Any], value).get(key) if isinstance(value, dict) else None
    return cast(dict[str, Any], found) if isinstance(found, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, required=True, help="正解 OM のディレクトリ")
    parser.add_argument("--pack", type=Path, help="実行で使った知識パック（その意味型は名付けた型に数えない）")
    parser.add_argument("--work", type=Path, default=Path("work"))
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    gold = load_ontology(args.gold)
    expected = {(t.name, n): c.semantic_type for t in gold.tables for n, c in (t.columns or {}).items()}
    packed = set(load_pack(args.pack).vocabulary.semantic_types or {}) if args.pack else set[str]()
    for source in args.sources:
        root = args.work / source
        runs = sorted((root / "runs").glob("*.json"))
        if not runs:
            raise SystemExit(f"{root}/runs に実行の記録が無い")
        tables: list[dict[str, Any]] = json.loads(runs[-1].read_text(encoding="utf-8"))["refine"]["tables"]
        born: dict[str, str] = {}
        spread: list[tuple[str, str, str]] = []
        for t in tables:
            table = str(t["table"])
            answer: object = t["answer"]
            for item, a in _part(_part(answer, "classify"), "answers").items():
                choice = str(cast(dict[str, Any], a)["choice"])
                if item.startswith("column ") and choice in born and born[choice] != table:
                    spread.append((table, item.removeprefix("column "), choice))
            for key in ("naming", "naming_retry"):
                named_types = cast(list[dict[str, Any]], _part(_part(answer, key), "data").get("semantic_types") or [])
                for named in named_types:
                    if (name := str(named["name"])) not in packed:
                        born.setdefault(name, table)
        final = {(t.name, n): c.semantic_type for t in load_ontology(root / "ontology").tables
                 for n, c in (t.columns or {}).items()}
        kept = {ty for tb, c, ty in spread if (tb, c) in expected and final.get((tb, c)) == ty}
        right = wrong = 0
        for ty in kept:
            columns = [k for k, v in final.items() if v == ty and k in expected]
            for i, x in enumerate(columns):
                for y in columns[i + 1:]:
                    right, wrong = (right + 1, wrong) if expected[x] == expected[y] else (right, wrong + 1)
        print(f"{source}: 名付けた型 {len(born)}、他の表へ広がった列 {len(spread)}")
        print(f"  広がりの多い型: {Counter(ty for _, _, ty in spread).most_common(8)}")
        print(f"  正解の表で、広がった型の列の組：正しい {right}、誤り {wrong}")


if __name__ == "__main__":
    main()
