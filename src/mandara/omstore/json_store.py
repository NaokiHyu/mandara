"""実行の記録・評価の結果・範囲などの JSON ファイル（work/ の下）。

Adapter 層。キーの順序を固定し、日本語はそのまま書く。書くときは一時ファイルに書いてから置き換える。
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mandara.omstore.codec import OmLoadError


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise OmLoadError(path, e) from e
