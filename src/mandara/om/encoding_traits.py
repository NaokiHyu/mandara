# 生成物。手で編集しない。元: src/mandara/om/schema/om.yaml / 生成: scripts/gen_om_model.sh
"""物理的な表現（SourceEncoding）ごとの、変換先の意味の型（ValueType の値）。"""

ENCODING_TARGETS: dict[str, str] = {
    'sqlserver_geography': 'geo.point',
}
