"""コーデックの法則：往復、不正な入力はエラー、①の SourceEncoding をすべて網羅。AdventureWorks の実データ。
登録したすべてのコーデック（registry.CODECS）に：decode が通る値は matches も真、encode → matches → decode で戻る、
そのコーデックで書いた見本はそのコーデックに判別される。判別が割れる場合は偽のコーデックで確かめる。"""

import struct
from collections.abc import Callable

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.codecs.geography import CodecError, decode_sqlserver_geography, encode_sqlserver_geography
from mandara.codecs.codec import Codec
from mandara.codecs.detect import detect
from mandara.codecs.registry import BY_ENCODING, CODECS
from mandara.om.encoding_traits import ENCODING_TARGETS
from mandara.om.model import SourceEncoding
from mandara.source.values import GeoPoint

points = st.builds(GeoPoint, lat=st.floats(-90, 90), lon=st.floats(-180, 180))


def test_every_encoding_has_a_decoder() -> None:
    assert set(BY_ENCODING) == {e.value for e in SourceEncoding} == set(ENCODING_TARGETS)


@given(points)
def test_roundtrip(point: GeoPoint) -> None:
    assert decode_sqlserver_geography(encode_sqlserver_geography(point)) == point


@pytest.mark.parametrize(("data", "reason"), [
    ("not bytes", "バイト列ではない"),
    (b"\x00" * 21, "単一の点"),
    (struct.pack("<iBBdd", 4269, 1, 0x0C, 1.0, 2.0), "SRID 4269"),
    (struct.pack("<iBBdd", 4326, 9, 0x0C, 1.0, 2.0), "版 9"),
    (struct.pack("<iBBdd", 4326, 1, 0x04, 1.0, 2.0), "単一の点ではない"),
    (struct.pack("<iBBdd", 4326, 1, 0x0C, float("nan"), 2.0), "有限"),
    (struct.pack("<iBBdd", 4326, 1, 0x0C, 91.0, 2.0), "範囲外"),
])
def test_invalid_input_is_an_error(data: object, reason: str) -> None:
    with pytest.raises(CodecError, match=reason):
        decode_sqlserver_geography(data)


def test_adventureworks_addresses() -> None:
    """住所 19,614 件がすべて点に戻り、Bothell（ワシントン州）の住所はその辺りにある。"""
    with psycopg.connect("dbname=adventureworks") as conn:
        rows = conn.execute("select city, spatiallocation from person.address").fetchall()
    decoded = [(city, decode_sqlserver_geography(value)) for city, value in rows]
    assert len(decoded) == 19614
    bothell = [p for city, p in decoded if city == "Bothell"]
    assert bothell and all(47 < p.lat < 48.5 and -123 < p.lon < -121.5 for p in bothell)


# ---------------------------------------------------------------- 登録したすべてのコーデックの法則
def _encode_point(value: object) -> bytes:
    assert isinstance(value, GeoPoint)
    return encode_sqlserver_geography(value)


ENCODERS: dict[str, tuple[st.SearchStrategy[object], Callable[[object], bytes]]] = {
    SourceEncoding.sqlserver_geography.value: (points, _encode_point),
}
"""法則のテストのための、encoding ごとの意味の値の生成と逆変換（Mandara はソースに書かないので、テストにだけ置く）。"""


def test_every_codec_has_an_encoder_for_the_laws() -> None:
    assert {c.encoding.value for c in CODECS} == set(ENCODERS)


def _near(codec: Codec) -> st.SearchStrategy[object]:
    """そのコーデックの形式に近い値（正しい値の1バイトを変えたもの、切り詰めたもの）と、任意の値。"""
    gen, encode = ENCODERS[codec.encoding.value]
    encoded = gen.map(encode)

    @st.composite
    def mutated(draw: st.DrawFn) -> object:
        data = bytearray(draw(encoded))
        i = draw(st.integers(0, len(data) - 1))
        data[i] = draw(st.integers(0, 255))
        return bytes(data)

    return st.one_of(encoded, mutated(), encoded.map(lambda b: b[:-1]), encoded.map(bytearray), encoded.map(memoryview),
                     st.binary(max_size=64), st.text(), st.integers(), st.none())


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.encoding.value)
def test_decodable_values_match(codec: Codec) -> None:
    """decode が通る値は matches も真（自動の判別が、読める値を見落とさない）。matches は例外を投げない。"""

    @given(_near(codec))
    def law(value: object) -> None:
        matched = codec.matches(value)
        try:
            codec.decode(value)
        except CodecError:
            return
        assert matched

    law()


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.encoding.value)
def test_encoded_values_match_and_round_trip(codec: Codec) -> None:
    gen, encode = ENCODERS[codec.encoding.value]

    @given(gen)
    def law(value: object) -> None:
        data = encode(value)
        assert codec.matches(data) and codec.decode(data) == value

    law()


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.encoding.value)
def test_encoded_samples_are_detected(codec: Codec) -> None:
    """そのコーデックで書いた値の見本（NULL まじり）は、そのコーデックに判別される（ほかのコーデックと取り違えない）。"""
    gen, encode = ENCODERS[codec.encoding.value]

    @given(st.lists(st.one_of(gen.map(encode), st.none()), min_size=1, max_size=20).filter(lambda v: any(v)))
    def law(values: list[object]) -> None:
        found = detect(values)
        assert (found.encoding, found.reason) == (codec.encoding.value, None)
        assert found.values + found.nulls == len(values)

    law()


# ---------------------------------------------------------------- 判別（偽のコーデックで、割れる場合を作る）

def _fake(encoding: SourceEncoding, prefix: bytes, readable: bool = True) -> Codec:
    def decode(value: object) -> object:
        if not (readable and isinstance(value, bytes) and value.startswith(prefix)):
            raise CodecError("読めない")
        return value

    return Codec(encoding, lambda v: isinstance(v, bytes) and v.startswith(prefix), decode)


GEO = SourceEncoding.sqlserver_geography


def test_detect_decides_only_when_exactly_one_codec_reads_every_value() -> None:
    a, b = _fake(GEO, b"A"), _fake(GEO, b"AB")
    assert detect([b"AB1", None, b"AB2"], [a]).encoding == GEO.value
    both = detect([b"AB1"], [a, b])
    assert both.encoding is None and "2 個" in (both.reason or "")
    partial = detect([b"A1", b"X"], [a])
    assert partial.encoding is None and "2 件中 形 1 件・読めた 1 件" in (partial.reason or "")
    unreadable = detect([b"A1"], [_fake(GEO, b"A", readable=False)])
    assert unreadable.encoding is None and "読めた 0 件" in (unreadable.reason or "")
    none = detect([b"X"], [a])
    assert (none.encoding, none.tallies, none.reason) == (None, (), "どのコーデックの形にも当たらない")
    empty = detect([None, None], [a])
    assert (empty.encoding, empty.nulls, empty.reason) == (None, 2, "NULL でない値が無い")
