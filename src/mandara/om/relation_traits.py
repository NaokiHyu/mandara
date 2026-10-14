# 生成物。手で編集しない。元: src/mandara/om/schema/om.yaml / 生成: scripts/gen_om_model.sh
"""関係型の性質（仕様 4.1）。キーは RelationType の値。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationTraits:
    transitive: bool
    inverse: str


RELATION_TRAITS: dict[str, RelationTraits] = {
    'is-a': RelationTraits(transitive=True, inverse='has-subtype'),
    'part-of': RelationTraits(transitive=True, inverse='has-part'),
    'has': RelationTraits(transitive=False, inverse='belongs-to'),
    'derived-from': RelationTraits(transitive=True, inverse='source-of'),
}
