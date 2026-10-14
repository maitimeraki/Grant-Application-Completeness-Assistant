from __future__ import annotations

from types import SimpleNamespace

from app.services.completeness import (
    MappingView,
    RequirementView,
    SupportingDocView,
    compute_completion,
)
from app.services.quotes import find_block_for_quote, normalize, quote_in_text


# ---------- quotes ----------

def test_normalize_whitespace_and_case():
    assert normalize("Hello   World\n") == "hello world"


def test_quote_in_text_tolerates_whitespace_and_quotes():
    text = 'The budget “must not” exceed 50,000 EUR.'
    assert quote_in_text('The budget "must not" exceed 50,000 EUR.', text)
    assert quote_in_text('budget "must not" exceed 50,000 EUR', text)
    assert not quote_in_text("budget must not exceed 50,000 EUR", text)  # quotes are real content
    assert not quote_in_text("totally unrelated sentence", text)


def test_find_block_prefers_shortest_match():
    blocks = [
        SimpleNamespace(id=1, text="Applicants must be registered non-profit organizations operating in the region. Extra details here."),
        SimpleNamespace(id=2, text="Applicants must be registered non-profit organizations."),
    ]
    found = find_block_for_quote("registered non-profit organizations", blocks)
    assert found.id == 2


# ---------- completion ----------

def _req(id, basis="mandatory", na=False, doc=None):
    return RequirementView(
        id=id, code=f"R{id}", title=f"Req {id}", basis=basis, not_applicable=na,
        required_supporting_doc_name=doc,
    )


def _map(id, status="confirmed", citations=1, weakness=None):
    return MappingView(
        requirement_id=id, status=status, weakness=weakness, verified_citation_count=citations
    )


def test_completion_all_satisfied():
    result = compute_completion(
        [_req(1), _req(2)], {1: _map(1), 2: _map(2)}, []
    )
    assert result["mandatory"]["percentage"] == 100.0
    assert result["mandatory"]["satisfied"] == 2
    assert "not an authoritative funding-eligibility" in result["disclaimer"]


def test_completion_open_unsatisfied_and_na():
    result = compute_completion(
        [_req(1), _req(2), _req(3), _req(4, na=True)],
        {
            1: _map(1),
            2: _map(2, status="suggested"),
            3: _map(3, status="rejected"),
        },
        [],
    )
    m = result["mandatory"]
    assert m["total"] == 3  # R4 N/A excluded
    assert m["satisfied"] == 1
    assert m["open"] == 1
    assert m["unsatisfied"] == 1
    assert m["not_applicable"] == 1
    assert abs(m["percentage"] - 33.3) < 0.1
    assert m["unsatisfied_items"][0]["reason"] == "rejected by reviewer"


def test_completion_requires_verified_citation():
    result = compute_completion([_req(1)], {1: _map(1, citations=0)}, [])
    assert result["mandatory"]["satisfied"] == 0
    assert result["attention"]["confirmed_without_verified_citation"] == 1


def test_completion_requires_supporting_doc_present():
    reqs = [_req(1, doc="Cover Letter")]
    docs_missing = [SupportingDocView(expected_name="Cover Letter", present=False, required=True)]
    docs_present = [SupportingDocView(expected_name="Cover Letter", present=True, required=True)]

    r1 = compute_completion(reqs, {1: _map(1)}, docs_missing)
    r2 = compute_completion(reqs, {1: _map(1)}, docs_present)
    assert r1["mandatory"]["satisfied"] == 0
    assert r2["mandatory"]["satisfied"] == 1


def test_completion_recommended_separate_from_mandatory():
    result = compute_completion(
        [_req(1, basis="mandatory"), _req(2, basis="recommended")],
        {1: _map(1)},  # only mandatory satisfied
        [],
    )
    assert result["mandatory"]["percentage"] == 100.0
    assert result["recommended"]["percentage"] == 0.0
    assert result["recommended"]["total"] == 1


def test_completion_empty_pool_is_none_not_zero():
    result = compute_completion([], {}, [])
    assert result["mandatory"]["percentage"] is None
    assert result["mandatory"]["total"] == 0


def test_completion_ambiguous_basis_counted_separately():
    result = compute_completion([_req(1, basis="ambiguous")], {}, [])
    assert result["ambiguous_basis_count"] == 1
    assert result["mandatory"]["total"] == 0
