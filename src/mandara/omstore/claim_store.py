"""主張台帳（仕様 6.6）の読み書き。

Adapter 層。配置：<root>/<name(schema)>/<name(table)>.yaml（root は work/<source>/claims、名前は符号化する）。
- 台帳は決定的な抽出器が丸ごと作り直すキャッシュ。書けるのは決定的な出所（schema / sql / codelist）の主張だけ。
  再生成できない主張（review など）を、書き直しで消さないため（保存方針は仕様 15.2 で未決）
- 書く前と読む境界の両方で、主張を検証する（台帳のファイルは手で編集されうる）
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

from mandara.om.claims import ClaimViolation, check_claims
from mandara.om.model import Claim, ClaimFile, ClaimSourceKind
from mandara.omstore import codec
from mandara.omstore.codec import OmLoadError
from mandara.omstore.tree import decode_name, encode_name, files_in, replace_tree

_DETERMINISTIC_KINDS = frozenset({ClaimSourceKind.schema.value, ClaimSourceKind.sql.value, ClaimSourceKind.codelist.value})
_WHAT = "主張台帳"


class InvalidClaimsError(Exception):
    """書こうとした・読んだ主張が、主張の検証（mandara.om.claims）を通らない。"""

    def __init__(self, violations: Sequence[ClaimViolation]) -> None:
        lines = "\n".join(f"  - {v.location}: {v.rule.name} {v.detail}" for v in violations)
        super().__init__(f"主張が不正（{len(violations)} 件）:\n{lines}")
        self.violations = tuple(violations)


def _layout(parts: tuple[str, ...], is_dir: bool) -> bool:
    return (len(parts) == 1 and is_dir) or (len(parts) == 2 and not is_dir and parts[1].endswith(".yaml"))


def parse_claims(text: str) -> ClaimFile:
    return codec.parse(text, ClaimFile)


def dump_claims(claims: ClaimFile) -> str:
    return codec.dump(claims)


def load_claim_ledger(root: Path, source: str) -> tuple[Claim, ...]:
    """台帳をすべて読む。配置との食い違い、主張の検証の違反はエラー。"""
    if not root.is_dir():
        # 台帳が無いことを、主張が0件であることと取り違えない
        raise OmLoadError(root, FileNotFoundError(f"{_WHAT}のディレクトリが無い"))
    claims: list[Claim] = []
    for path in files_in(root, _layout, _WHAT):
        try:
            schema, table = decode_name(path.parent.name), decode_name(path.stem)
        except ValueError as e:
            raise OmLoadError(path, e) from e
        for claim in codec.load(path, ClaimFile).claims or []:
            subject = claim.subject
            if (subject.source, subject.schema_name, subject.table) != (source, schema, table):
                raise OmLoadError(path, ValueError(
                    f"{claim.id} の主語 {subject.source}:{subject.schema_name}.{subject.table} が"
                    f"配置 {source}:{schema}.{table} と食い違う"))
            claims.append(claim)
    if violations := check_claims(claims):
        raise OmLoadError(root, InvalidClaimsError(violations))
    return tuple(claims)


def write_claim_ledger(root: Path, source: str, claims: Iterable[Claim]) -> None:
    """台帳を丸ごと書き直す。書く前に主張を検証し、root に台帳以外のものがあれば消さずにエラーにする。"""
    claims = list(claims)
    if foreign := sorted({c.subject.source for c in claims} - {source}):
        raise ValueError(f"ソース {source} の台帳に、別のソースの主張がある: {foreign}")
    if kinds := sorted({c.source.kind for c in claims} - _DETERMINISTIC_KINDS):
        raise ValueError(f"再生成できない出所の主張は台帳に書けない（書き直しで消える）: {kinds}")
    if violations := check_claims(claims):
        raise InvalidClaimsError(violations)

    by_table: dict[tuple[str, str], list[Claim]] = {}
    for claim in claims:
        by_table.setdefault((claim.subject.schema_name, claim.subject.table), []).append(claim)
    files = {
        (encode_name(schema), f"{encode_name(table)}.yaml"): dump_claims(ClaimFile(
            claims=sorted(group, key=lambda c: (c.subject.column or "", c.predicate, c.id))))
        for (schema, table), group in by_table.items()
    }
    replace_tree(root, files, _layout, _WHAT)
