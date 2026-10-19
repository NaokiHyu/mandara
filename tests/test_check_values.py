"""PostgreSQL の CHECK の式から 1 列の値の一覧を読む（mandara.connectors.postgres.check_values）と、その主張（allows_values）。"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.connectors.postgres.check_values import allowed_values
from mandara.extractors import catalog as catalog_extractor
from mandara.modeler import classify
from mandara.om.claims import check_claims
from mandara.om.model import ClaimPredicate, ObjectKind, SemanticType, TypeFamily, ValueType
from mandara.source.catalog import AllowedValues, Catalog, Check, Column, Key, Scope, Table

_NAMES = st.from_regex(r"c_[a-z0-9_]{0,10}", fullmatch=True)
"""PostgreSQL が引用符なしで書く列名（予約語は引用符つきで書くので、ここでは c_ で始めて予約語を避ける）。"""
_CODES = st.lists(st.from_regex(r"[A-Z]{1,3}", fullmatch=True), min_size=1, max_size=6)


def _quoted(values: list[str]) -> str:
    return ", ".join(f"'{v}'::text" for v in values)


@given(_NAMES, _CODES, st.sampled_from(["any", "in", "upper", "null_first", "null_last"]))
def test_value_lists_are_read_in_the_forms_postgres_writes(column: str, values: list[str], form: str) -> None:
    """法則：PostgreSQL が書く値の一覧の CHECK（= ANY (ARRAY[...])、IN、upper と型変換の包み、OR 列 IS NULL）は、
    書いた列と値の一覧に読める。"""
    listed = f"({column} = ANY (ARRAY[{_quoted(values)}]))"
    expression = {
        "any": listed,
        "in": f"({column} IN ({', '.join(repr(v) for v in values)}))",
        "upper": f"(upper(({column})::text) = ANY (ARRAY[{_quoted(values)}]))",
        "null_first": f"(({column} IS NULL) OR {listed})",
        "null_last": f"({listed} OR ({column} IS NULL))",
    }[form]
    found = allowed_values(expression)
    assert found is not None and (found.column, found.values) == (column, tuple(values))
    assert found.normalized == (form == "upper")  # 揃えてから比べる CHECK は、列の値そのものを限らない


@pytest.mark.parametrize("expression", [
    "((orderqty > 0))",
    "((c >= 0) AND (c <= 120))",
    "((x IS NULL) OR (y = ANY (ARRAY['a'::text])))",  # NULL の列が別
    "((x = ANY (ARRAY['a'::text])) OR (x IS NOT NULL))",  # IS NOT NULL は値を限らない
    "(x IN ('abc",  # 字句の誤り（読めない CHECK でカタログを止めない）
    "(x = ANY (ARRAY['a'::text, NULL::text]))",  # NULL を値に含む
    "((x = ANY (ARRAY['a'::text])) AND (y > 0))",
    "(lower(x || y) = ANY (ARRAY['a'::text]))",  # 列が 1 つではない
    "garbage (((",
])
def test_other_checks_are_not_value_lists(expression: str) -> None:
    assert allowed_values(expression) is None


def _table(*checks: Check) -> Table:
    columns = (Column("id", 1, "integer", TypeFamily.integer, False, None, None, None),
               Column("gender", 2, "text", TypeFamily.text, False, None, None, None))
    return Table("s", "t", ObjectKind.table, None, columns, Key("pk", ("id",)), (), (), checks)


def test_the_extractor_makes_a_column_claim_for_each_value_list() -> None:
    claims = catalog_extractor.extract("src", Catalog(Scope(("s",)), (_table(
        Check("ck_gender", "(gender = ANY (ARRAY['M'::text]))", AllowedValues("gender", ("M", "F"), True)),
        Check("ck_id", "(id > 0)")),)))
    assert check_claims(claims) == []
    [listed] = [c for c in claims if c.predicate == ClaimPredicate.allows_values.value]
    assert listed.subject.column == "gender" and listed.allowed_values == ["M", "F"] and listed.values_normalized
    assert sum(c.predicate == ClaimPredicate.checks.value for c in claims) == 2  # 式の主張はそのまま残る
    with pytest.raises(ValueError, match="コネクタの誤り"):
        catalog_extractor.extract("src", Catalog(Scope(("s",)), (_table(
            Check("ck_x", "(x = ANY (ARRAY['M'::text]))", AllowedValues("x", ("M",), False))),)))


def test_pack_types_are_not_offered_for_listed_columns() -> None:
    code = SemanticType(name="gender_code", type=ValueType.code, pack="p 1")
    mine = SemanticType(name="status_code", type=ValueType.code)
    assert not classify.pack_fits(code, "text", listed=True) and classify.pack_fits(code, "text")
    assert classify.pack_fits(mine, "text", listed=True)
