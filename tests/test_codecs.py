"""コーデックの法則：往復、不正な入力はエラー、①の SourceEncoding をすべて網羅。AdventureWorks の実データ。"""

import struct

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.codecs.geography import CodecError, decode_sqlserver_geography, encode_sqlserver_geography
from mandara.codecs.registry import DECODERS
from mandara.om.encoding_traits import ENCODING_TARGETS
from mandara.om.model import SourceEncoding
from mandara.source.values import GeoPoint

points = st.builds(GeoPoint, lat=st.floats(-90, 90), lon=st.floats(-180, 180))


def test_every_encoding_has_a_decoder() -> None:
    assert set(DECODERS) == {e.value for e in SourceEncoding} == set(ENCODING_TARGETS)


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
