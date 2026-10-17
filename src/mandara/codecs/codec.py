"""コーデック：物理的な表現（①の SourceEncoding）ひとつぶんの、判定と変換（仕様 4.2）。

Domain 層。I/O の無い純粋な関数の組。どの DB から読んだ値でもよい（DB コネクタは形式を知らない）。
コーデックを足すには、mandara.codecs の下にモジュールを1つ作り、registry.CODECS に1行足す。
encoding の名前は①の閉じた集合なので、足す前に①の SourceEncoding に値（と decodes_to）を足す。

法則（tests/test_codecs.py で、登録したすべてのコーデックに検査する）：
- decode が通る値は、matches も真（matches は decode が受け付ける値を含む。matches だけ真で decode が通らない値はありうる）
- encode できるコーデックは、encode → matches → decode で元の値に戻る
"""

from collections.abc import Callable
from dataclasses import dataclass

from mandara.om.model import SourceEncoding


class CodecError(Exception):
    """値を意味の型に変換できない（形式が違う、未対応の形）。"""


@dataclass(frozen=True)
class Codec:
    encoding: SourceEncoding
    matches: Callable[[object], bool]
    """値がこの形式に見えるか（形だけの安い判定。例外を投げない）。自動の判別（mandara.codecs.detect）に使う。"""
    decode: Callable[[object], object]
    """意味の型の値にする。変換できなければ CodecError。"""
