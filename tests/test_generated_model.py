"""生成物（src/mandara/om の model.py, relation_traits.py, encoding_traits.py と、ui/src/om の model.ts, om.schema.json）がメタモデル①と食い違っていないこと。"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
GENERATED = ("model.py", "relation_traits.py", "encoding_traits.py")


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("om")
    subprocess.run([ROOT / "scripts/gen_om_model.sh", out], check=True, capture_output=True)
    return out


@pytest.mark.parametrize("name", GENERATED)
def test_generated_file_is_up_to_date(regenerated: Path, name: str) -> None:
    committed = (ROOT / "src/mandara/om" / name).read_text(encoding="utf-8")
    assert (regenerated / name).read_text(encoding="utf-8") == committed, "scripts/gen_om_model.sh で再生成すること"


UI_GENERATED = ("model.ts", "om.schema.json", "index.schema.json")


@pytest.fixture(scope="module")
def regenerated_ui(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ui")
    subprocess.run([ROOT / "scripts/gen_ui_types.sh", out], check=True, capture_output=True)
    return out


@pytest.mark.parametrize("name", UI_GENERATED)
def test_ui_generated_file_is_up_to_date(regenerated_ui: Path, name: str) -> None:
    committed = (ROOT / "ui/src/om" / name).read_text(encoding="utf-8")
    assert (regenerated_ui / name).read_text(encoding="utf-8") == committed, "scripts/gen_ui_types.sh で再生成すること"
