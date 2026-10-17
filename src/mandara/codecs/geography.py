"""SQL Server の geography 形式 → GeoPoint のコーデック（仕様 4.2：変換は encoding ごとのコーデックの責務。DB コネクタは形式を知らない）。

Domain 層：I/O の無い純粋な変換。どの DB から読んだバイト列でもよい。登録表は mandara.codecs.registry。

sqlserver_geography：SQL Server の geography 型のシリアル化形式のうち、単一の点（22 バイト）。
  SRID（int32 LE）・版（1 バイト）・属性（1 バイト。0x0C = 有効 + 単一の点）・緯度（float64 LE）・経度（float64 LE）。
  SRID は 4326（WGS84）だけを受け付ける。点以外（線、多角形）や Z / M 値を持つものは未対応としてエラーにする。
"""

import math
import struct
from typing import cast

from mandara.codecs.codec import Codec, CodecError
from mandara.om.model import SourceEncoding
from mandara.source.values import GeoPoint

WGS84 = 4326
_POINT = struct.Struct("<iBBdd")
_VALID_SINGLE_POINT = 0x0C
_HEADER_VERSIONS = (1, 2)


def matches_sqlserver_geography(value: object) -> bool:
    """22 バイトで、先頭が SRID 4326・版 1 か 2・単一の点の属性（版 1 なら E6100000010C）。"""
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, (bytearray, memoryview)):
        data = bytes(cast(bytearray, value))
    else:
        return False
    return (len(data) == _POINT.size and int.from_bytes(data[:4], "little", signed=True) == WGS84
            and data[4] in _HEADER_VERSIONS and data[5] == _VALID_SINGLE_POINT)


def decode_sqlserver_geography(value: object) -> GeoPoint:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, (bytearray, memoryview)):
        data = bytes(cast(bytearray, value))
    else:
        raise CodecError(f"バイト列ではない: {type(value).__name__}")
    if len(data) != _POINT.size:
        raise CodecError(f"単一の点（{_POINT.size} バイト）ではない: {len(data)} バイト（線・多角形・Z/M 値は未対応）")
    srid, version, properties, lat, lon = _POINT.unpack(data)
    if srid != WGS84:
        raise CodecError(f"SRID {srid} は未対応（{WGS84} だけ）")
    if version not in _HEADER_VERSIONS:
        raise CodecError(f"版 {version} は未対応")
    if properties != _VALID_SINGLE_POINT:
        raise CodecError(f"属性 0x{properties:02X} は単一の点ではない")
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise CodecError("緯度・経度が有限の数ではない")
    try:
        return GeoPoint(lat=lat, lon=lon)
    except ValueError as e:
        raise CodecError(str(e)) from e


def encode_sqlserver_geography(point: GeoPoint) -> bytes:
    """テストと検証のための逆変換（Mandara はソースに書かない）。"""
    return _POINT.pack(WGS84, 1, _VALID_SINGLE_POINT, point.lat, point.lon)


SQLSERVER_GEOGRAPHY = Codec(SourceEncoding.sqlserver_geography, matches_sqlserver_geography, decode_sqlserver_geography)
