"""地図の下地（ui/public/geo）を Natural Earth から作り直す。

元：https://github.com/nvkelso/natural-earth-vector のタグ v5.1.2（パブリックドメイン）。元ファイルの SHA-256 を確かめてから、
属性を消し、座標を小数第3位（約 100 m）に丸める。同じ元からは、同じ出力になる。
使い方: uv run python scripts/prepare_basemap.py
"""

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
SOURCES: dict[str, tuple[str, str]] = {
    "countries.geojson": ("ne_50m_admin_0_countries.geojson", "3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb"),
    "states.geojson": ("ne_50m_admin_1_states_provinces_lines.geojson", "72cca93c850d412628a5da4bc5ebfe21ba4d376eb34611bde6b623ee73f0fdcf"),
}
OUT = Path(__file__).parent.parent / "ui/public/geo"


def _round(coordinates: Any) -> Any:
    if isinstance(coordinates[0], list):
        return [_round(c) for c in coordinates]
    return [round(coordinates[0], 3), round(coordinates[1], 3)]


def main() -> None:
    for out_name, (source, expected) in SOURCES.items():
        with urllib.request.urlopen(BASE + source, timeout=60) as response:
            raw = response.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            raise SystemExit(f"{source} の SHA-256 が違う: {digest}（期待 {expected}）")
        data = json.loads(raw)
        features = [
            {"type": "Feature", "properties": {},
             "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])}}
            for f in data["features"] if f.get("geometry")
        ]
        (OUT / out_name).write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
        print(f"{out_name}: {len(features)} features, source sha256 {digest}")


if __name__ == "__main__":
    main()
