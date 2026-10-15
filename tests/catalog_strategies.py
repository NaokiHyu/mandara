"""DB に依存しないカタログ（mandara.source.catalog）を生成する Hypothesis の戦略。

コネクタを通さずに、抽出器・モデラーの法則を検証するために使う。
- 名前には、YAML や識別子で化けやすいもの（on、"..", "a/b", 引用符、"."、日本語）を混ぜる
- 複数スキーマ。スキーマをまたぐ参照と自己参照を含む。中身が同じで名前の違う制約も作りうる
"""

from hypothesis import strategies as st

from mandara.om.model import ObjectKind, ReferentialAction, TypeFamily
from mandara.source.catalog import Catalog, Check, Column, ForeignKey, Key, Scope, Table

names = st.one_of(
    st.sampled_from(("on", "no", "null", "Mixed", "名前", "a b", "..", "a/b", 'q"t', "x.y")),
    st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True),
)
texts = st.text(st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=1, max_size=20)


@st.composite
def tables(draw: st.DrawFn, schema: str, name: str, others: list[tuple[str, str, tuple[str, ...]]]) -> Table:
    """others は (schema, table, 主キー) の一覧。外部キーの参照先は others と自分自身（自己参照）から選ぶ。"""
    kind = draw(st.sampled_from(tuple(ObjectKind)))
    column_names = draw(st.lists(names, min_size=1, max_size=5, unique=True))
    columns = tuple(
        Column(
            name=n,
            position=i + 1,
            source_type=draw(texts),
            family=draw(st.sampled_from(tuple(TypeFamily))),
            nullable=draw(st.booleans()) if kind is ObjectKind.table else None,
            default=draw(st.none() | texts),
            comment=draw(st.none() | texts),
            type_alias=draw(st.none() | texts),
        )
        for i, n in enumerate(column_names)
    )
    keys = st.lists(st.sampled_from(column_names), min_size=1, max_size=2, unique=True).map(tuple)
    # 制約の名前は一意。中身が同じ制約（同じ列の一意が2つなど）は作りうる
    constraint_names = iter(draw(st.lists(names, min_size=12, max_size=12, unique=True)))
    primary_key = Key(next(constraint_names), draw(keys)) if draw(st.booleans()) else None
    targets = [*others, *([(schema, name, primary_key.columns)] if primary_key else [])]
    foreign_keys = tuple(
        ForeignKey(next(constraint_names), tuple(draw(st.sampled_from(column_names)) for _ in target_key),
                   target_schema, target_table, target_key,
                   draw(st.sampled_from(tuple(ReferentialAction))), draw(st.sampled_from(tuple(ReferentialAction))))
        for target_schema, target_table, target_key in (
            draw(st.lists(st.sampled_from(targets), max_size=3)) if targets else [])
    )
    return Table(
        schema=schema,
        name=name,
        kind=kind,
        comment=draw(st.none() | texts),
        columns=columns,
        primary_key=primary_key,
        uniques=tuple(sorted((Key(next(constraint_names), k) for k in draw(st.lists(keys, max_size=3))),
                             key=lambda k: k.name)),
        foreign_keys=tuple(sorted(foreign_keys, key=lambda f: f.name)),
        checks=tuple(sorted((Check(next(constraint_names), draw(texts)) for _ in range(draw(st.integers(0, 3)))),
                            key=lambda c: c.name)),
    )


@st.composite
def catalogs(draw: st.DrawFn) -> Catalog:
    """複数スキーマ。スキーマをまたぐ参照と自己参照を含む。"""
    qualified = draw(st.lists(st.tuples(st.sampled_from(("s1", "s.2", "スキーマ")), names), min_size=1, max_size=5,
                              unique=True))
    built: list[Table] = []
    for schema, name in qualified:
        others = [(t.schema, t.name, t.primary_key.columns) for t in built if t.primary_key is not None]
        built.append(draw(tables(schema, name, others)))
    schemas = tuple(sorted({s for s, _ in qualified}))
    return Catalog(scope=Scope(schemas), tables=tuple(sorted(built, key=lambda t: (t.schema, t.name))))
