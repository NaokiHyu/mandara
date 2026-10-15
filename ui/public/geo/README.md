# 地図の下地（オフライン）

出典：Natural Earth（パブリックドメイン）、https://github.com/nvkelso/natural-earth-vector のタグ v5.1.2

| ファイル | 元 | 元の SHA-256 |
|---|---|---|
| countries.geojson | geojson/ne_50m_admin_0_countries.geojson | 3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb |
| states.geojson | geojson/ne_50m_admin_1_states_provinces_lines.geojson | 72cca93c850d412628a5da4bc5ebfe21ba4d376eb34611bde6b623ee73f0fdcf |

加工：属性を消し、座標を小数第3位（約 100 m）に丸めた。作り直しは `uv run python scripts/prepare_basemap.py`
（元の SHA-256 を確かめ、同じ元からは同じ出力になる）。

外部のタイル配信を使わない（誰がどの地域を見たかを外に出さない）。街路は無い。
Natural Earth の国境は「事実上の支配」に基づく。係争地の扱いは Natural Earth の方針に従っており、Mandara の立場ではない。
