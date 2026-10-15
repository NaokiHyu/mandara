"""物理的な表現（①の SourceEncoding）→ コーデックの登録表。

Domain 層。コーデックは、ソースの値を意味の型（①の ValueType）の値に変える純粋な関数。
変換先の型は①の encoding_traits（ENCODING_TARGETS）が決めている。登録表の網羅はテストで検査する。
"""

from collections.abc import Callable

from mandara.codecs.geography import CodecError, decode_sqlserver_geography
from mandara.om.model import SourceEncoding

Decoder = Callable[[object], object]

DECODERS: dict[str, Decoder] = {
    SourceEncoding.sqlserver_geography.value: decode_sqlserver_geography,
}

__all__ = ["DECODERS", "CodecError", "Decoder"]
