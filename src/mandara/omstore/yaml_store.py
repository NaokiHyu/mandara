"""OM ファイル（仕様 5章）の読み書き。

Adapter 層。
- 配置：vocabulary.yaml と entity_groups/<entity_group>/tables/<source>/<table>.yaml。中身の entity_group / source / name と一致させる。
  ディレクトリ名・ファイル名の部品は名前を符号化する（mandara.omstore.tree。安全な名前はそのまま）
- 配置規則に合わないファイル・シンボリックリンクはエラー。黙って読み飛ばさない
- 生成した OM（work/<source>/ontology）を書くときは丸ごと置き換える。ただし人の判断（approved / rejected）を
  含む OM は上書きしない（再実行での統合は T6.1 の責務）
- レビューの結果を書くときは、今ある OM との違いが判断だけであることを確かめてから置き換える
"""

from collections.abc import Iterator, Sequence
from pathlib import Path

from mandara.om.invariants import Violation, check
from mandara.om.model import ReviewStatus, Table, Vocabulary
from mandara.om.names import split_qualified
from mandara.om.ontology import Ontology
from mandara.review.decisions import review_changes
from mandara.omstore import codec
from mandara.omstore.codec import OmLoadError
from mandara.omstore.tree import decode_name, encode_name, files_in, replace_tree

_WHAT = "OM"
_VOCABULARY = "vocabulary.yaml"
_ENTITY_GROUPS = "entity_groups"


class InvalidOntologyError(Exception):
    """書こうとした OM が検証（4.5）を通らない。"""

    def __init__(self, violations: Sequence[Violation]) -> None:
        lines = "\n".join(f"  - {v.location}: {v.rule.name} {v.detail}" for v in violations)
        super().__init__(f"OM が不正（{len(violations)} 件）:\n{lines}")
        self.violations = tuple(violations)


class OntologyWriteRefusedError(Exception):
    """OM を書けない（人の判断を含む既存の OM、同じファイルに当たる2つの表）。"""


def _layout(parts: tuple[str, ...], is_dir: bool) -> bool:
    if parts == (_VOCABULARY,):
        return not is_dir
    if not parts or parts[0] != _ENTITY_GROUPS or len(parts) > 5 or (len(parts) >= 3 and parts[2] != "tables"):
        return False
    return is_dir if len(parts) < 5 else (not is_dir and parts[4].endswith(".yaml"))


# ---------------------------------------------------------------- 読み


def parse_table(text: str) -> Table:
    return codec.parse(text, Table)


def parse_vocabulary(text: str) -> Vocabulary:
    return codec.parse(text, Vocabulary)


def load_table(path: Path) -> Table:
    return codec.load(path, Table)


def load_vocabulary(path: Path) -> Vocabulary:
    return codec.load(path, Vocabulary)


def load_ontology(root: Path) -> Ontology:
    """root は仕様 5章の ontology/ に当たるディレクトリ。"""
    tables: list[Table] = []
    for path in files_in(root, _layout, _WHAT):
        parts = path.relative_to(root).parts
        if parts == (_VOCABULARY,):
            continue
        table = load_table(path)
        try:
            entity_group, source, table_name = decode_name(parts[1]), decode_name(parts[3]), decode_name(path.stem)
            actual_part = _table_part(table)
        except ValueError as e:
            raise OmLoadError(path, e) from e
        _check_placement(path, table, entity_group=entity_group, source=source, table_name=table_name, actual_part=actual_part)
        tables.append(table)
    return Ontology(vocabulary=load_vocabulary(root / _VOCABULARY), tables=tuple(tables))


def _check_placement(
    path: Path, table: Table, *, entity_group: str, source: str, table_name: str, actual_part: str
) -> None:
    expected = {"entity_group": entity_group, "source": source, "name の表名部分": table_name}
    actual = {"entity_group": table.entity_group, "source": table.source, "name の表名部分": actual_part}
    mismatched = [f"{k}: 配置={expected[k]!r}, 中身={actual[k]!r}" for k in expected if expected[k] != actual[k]]
    if mismatched:
        raise OmLoadError(path, ValueError("配置と中身が食い違う: " + "; ".join(mismatched)))


def _table_part(table: Table) -> str:
    """name の最後の部品（表名）。name の書き方は mandara.om.names。"""
    return split_qualified(table.name)[-1]


# ---------------------------------------------------------------- 書き


def dump_table(table: Table) -> str:
    return codec.dump(table)


def dump_vocabulary(vocabulary: Vocabulary) -> str:
    return codec.dump(vocabulary)


def write_ontology(root: Path, ontology: Ontology) -> None:
    """生成した OM で root を丸ごと置き換える。書く前に検証し、人の判断を含む既存の OM は上書きしない。"""
    if violations := check(ontology):
        raise InvalidOntologyError(violations)
    if root.exists() and (decided := list(_human_decisions(load_ontology(root)))):
        raise OntologyWriteRefusedError(
            f"人の判断（approved / rejected）を含む OM は上書きしない（{len(decided)} 件。例 {decided[:3]}）。"
            "再実行での統合は T6.1")
    _replace(root, ontology)


def write_reviewed_ontology(root: Path, ontology: Ontology) -> None:
    """レビューの結果を書く。今ある OM との違いが判断（status、reviewed_*、未決だった表の種類）だけであることを確かめる。"""
    if violations := check(ontology):
        raise InvalidOntologyError(violations)
    if problems := review_changes(load_ontology(root), ontology):
        raise OntologyWriteRefusedError("レビューで判断以外が変わろうとした: " + "; ".join(problems[:5]))
    _replace(root, ontology)


def _replace(root: Path, ontology: Ontology) -> None:
    files: dict[tuple[str, ...], str] = {(_VOCABULARY,): dump_vocabulary(ontology.vocabulary)}
    for table in ontology.tables:
        parts = (_ENTITY_GROUPS, encode_name(table.entity_group), "tables", encode_name(table.source),
                 f"{encode_name(_table_part(table))}.yaml")
        if parts in files:
            raise OntologyWriteRefusedError(f"同じファイルに2つの表が当たる: {'/'.join(parts)}")
        files[parts] = dump_table(table)
    replace_tree(root, files, _layout, _WHAT)


def _human_decisions(ontology: Ontology) -> Iterator[str]:
    return (where for where, a in ontology.annotations() if a.status != ReviewStatus.proposed.value)
