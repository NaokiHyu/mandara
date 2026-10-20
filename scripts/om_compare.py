"""推論の実行（work/<source>）を並べて比べる表を出す（T3.10 の測定）。

列：列の意味型と意味型の組の「当たり・誤り率」、表の種類の当たり、確信度の帯ごとの件数、閾値 0.9 / 0.8 での要レビューの件数、
列の意味型の較正（0.9 帯と 0.7 帯の当たり / 数）、費用（円。為替 150 円/ドル）。
1 回ずつの差は揺れの中にあることが多い（T3.10）。同じ条件を複数回回して比べること。
使い方: uv run python scripts/om_compare.py --gold work/gold_bare3 aw_bare4_pack4 aw_bare4_parts
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from mandara.evaluation.score import calibrate, score
from mandara.omstore.yaml_store import load_ontology
from mandara.review.decisions import is_bulk_candidate, questions

YEN_PER_USD = 150


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, required=True, help="正解 OM のディレクトリ")
    parser.add_argument("--work", type=Path, default=Path("work"))
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    gold = load_ontology(args.gold)
    print(f"{'source':18} {'型 当たり/誤%':>12} {'組 当たり/誤%':>12} {'種類':>4} {'0.6':>4} {'0.7':>4} {'0.8':>4} {'0.9+':>5} "
          f"{'要rev@0.9':>9} {'要rev@0.8':>9} {'較正0.9':>7} {'較正0.7':>7} {'円':>6}")
    for source in args.sources:
        root = args.work / source
        ontology = load_ontology(root / "ontology")
        aspects = {a.aspect: a for a in score(ontology, gold).aspects}

        def hit(aspect: str) -> str:
            a = aspects[aspect]
            return f"{a.correct}/{len(a.wrong) * 100 // max(a.proposed, 1)}%"

        pending = questions(ontology)
        bands = Counter(min(int((q.confidence or 0) * 10), 9) for q in pending)
        calibration = calibrate(ontology, gold).aspects["column_type"]

        def band(low: float) -> str:
            b = next(b for b in calibration if abs(b.low - low) < 1e-9)
            return f"{b.correct}/{b.proposed}"

        runs = sorted((root / "runs").glob("*.json"))
        if not runs:
            raise SystemExit(f"{root}/runs に実行の記録が無い")
        llm = json.loads(runs[-1].read_text(encoding="utf-8"))["llm"]
        yen = llm["cost_usd"] * YEN_PER_USD
        review = {t: sum(not is_bulk_candidate(q, t) for q in pending) for t in (0.9, 0.8)}
        print(f"{source:18} {hit('column_types'):>12} {hit('semantic_type_pairs'):>12} {aspects['kind'].correct:>4} "
              f"{bands[6]:>4} {bands[7]:>4} {bands[8]:>4} {bands[9]:>5} {review[0.9]:>9} {review[0.8]:>9} "
              f"{band(0.9):>7} {band(0.7):>7} {yen:>6.2f}")


if __name__ == "__main__":
    main()
