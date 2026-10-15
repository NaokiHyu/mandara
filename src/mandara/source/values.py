"""ソースの値を、意味の型（①の ValueType）として表したもの。

Domain 層。物理的な表現（バイナリの形式など）はここに持ち込まない。変換は mandara.codecs（Adapter）の責務。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GeoPoint:
    """地理上の点（ValueType geo.point）。WGS84 の緯度・経度（度）。"""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0):
            raise ValueError(f"緯度・経度の範囲外: ({self.lat}, {self.lon})")
