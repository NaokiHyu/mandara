"""生成物（src/mandara/om の model.py, relation_traits.py, encoding_traits.py）がメタモデル①と食い違っていないこと。"""

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
