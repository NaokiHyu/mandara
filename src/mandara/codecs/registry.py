"""登録したコーデック（mandara.codecs.codec.Codec）の一覧。コーデックを足すときは、ここに1行足す。

Domain 層。変換先の型は①の encoding_traits（ENCODING_TARGETS）が決めている。
①の SourceEncoding のどの値にも、ちょうど1つのコーデックがあること（tests/test_codecs.py で検査する）。
"""

from mandara.codecs.codec import Codec, CodecError
from mandara.codecs.geography import SQLSERVER_GEOGRAPHY

CODECS: tuple[Codec, ...] = (
    SQLSERVER_GEOGRAPHY,
)

BY_ENCODING: dict[str, Codec] = {c.encoding.value: c for c in CODECS}
if len(BY_ENCODING) != len(CODECS):
    raise AssertionError(f"同じ encoding のコーデックが2つ以上ある: {sorted(c.encoding.value for c in CODECS)}")

__all__ = ["BY_ENCODING", "CODECS", "Codec", "CodecError"]
