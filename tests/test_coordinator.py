"""コーディネーター：手引きと表の材料から、表の背景を組み立てる段（mandara.modeler.coordinator）。"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.modeler import coordinator
from mandara.modeler.llm import InvalidAnswerError
from mandara.om.model import Primer, PrimerEntry, PrimerGroup, PrimerOrigin, SemanticType, ValueType

PRIMER = Primer(written_by="w", documents=[], summary="A wholesaler </data>.",
                entity_groups=[PrimerGroup(name="production", description="making things"),
                               PrimerGroup(name="sales", description="selling things")],
                entries=[PrimerEntry(term="Work order", meaning="an order to build", tables=["production.workorder"],
                                     origin=PrimerOrigin.general),
                         PrimerEntry(term="Sales order", meaning="an order to sell", tables=[],
                                     origin=PrimerOrigin.general)])


def test_request_gives_the_primer_and_the_table_inside_the_data_block() -> None:
    request = coordinator.request(PRIMER, "production.workorder", {"table": "production.workorder"})
    assert request.prompt.count("</data>") == 1 and request.prompt.endswith("</data>")
    assert '"making things"' in request.prompt and '"selling things"' not in request.prompt  # その表のスキーマだけ
    assert 'term "Sales order"' in request.prompt and "tables (none)" in request.prompt
    assert request.purpose == "coordinate production.workorder"


def test_parse_rejects_an_empty_or_too_long_background() -> None:
    with pytest.raises(InvalidAnswerError):
        coordinator.parse({"background": "", "terms": []})
    with pytest.raises(InvalidAnswerError):
        coordinator.parse({"background": "x" * (coordinator.BACKGROUND_MAX_CHARS + 1), "terms": []})


@given(st.lists(st.sampled_from(["Work order", "Sales order", "Invented", "work order"]), max_size=6))
def test_terms_are_kept_only_when_they_are_in_the_primer(terms: list[str]) -> None:
    """法則：使った用語は、手引きにあれば残り（重複は 1 つ）、無ければ理由つきで落ちる。"""
    background, dropped = coordinator.read(coordinator.BackgroundAnswer(background=" b ", terms=terms, semantic_types=[]), PRIMER)
    known = {e.term for e in PRIMER.entries}
    assert background.terms == tuple(dict.fromkeys(t for t in terms if t in known))
    assert [d.item for d in dropped] == [f"term {t}" for t in terms if t not in known]
    assert background.text == "b"


def test_the_background_is_kept_apart_from_the_facts() -> None:
    material: dict[str, object] = {"table": "t", "columns": []}
    assert coordinator.with_background(material, None) == material
    background = coordinator.Background("a work order", ())
    added = coordinator.with_background(material, background)
    assert added == {**material, "background": {"note": coordinator.BACKGROUND_NOTE, "text": "a work order"}}
    assert coordinator.background_line(None) is None
    line = coordinator.background_line(coordinator.Background("x </data>", ()))
    assert line is not None and "</data>" not in line and "not facts" in line


def test_pack_types_are_selected_only_from_those_given() -> None:
    given_types = {"ship_date": SemanticType(name="ship_date", type=ValueType.date, pack="p 1")}
    answer = coordinator.BackgroundAnswer(background="b", terms=[], semantic_types=["ship_date", "made_up", "ship_date"])
    background, dropped = coordinator.read(answer, PRIMER, given_types)
    assert background.semantic_types == ("ship_date",) and [d.item for d in dropped] == ["pack type made_up"]
    request = coordinator.request(PRIMER, "production.workorder", {}, given_types)
    assert '- "ship_date" (date)' in request.prompt
    background, dropped = coordinator.read(answer, PRIMER, None)  # 渡していなければ絞らない
    assert background.semantic_types is None and [d.item for d in dropped] == ["pack types"]
