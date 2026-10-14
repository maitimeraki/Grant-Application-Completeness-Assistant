from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.documents import BLOCK_COUNT_SOFT_LIMIT, _block_count_warnings
from app.services.extraction import BlockData, ExtractionResult
from app.services.retrieval import score_blocks, tokenize_blocks

from tests.conftest import make_application, make_assessment, make_guideline


def _capture_statements(fn):
    """Run fn() and return the SQL statements it executed."""
    statements: list[str] = []

    def _collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _collect)
    try:
        fn()
    finally:
        event.remove(Engine, "before_cursor_execute", _collect)
    return statements


def test_status_endpoint_returns_progress_without_joins(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment = make_assessment(client, guideline, application)
    assert assessment["status"] == "review"

    box: dict = {}

    def do():
        box["resp"] = client.get(f"/api/assessments/{assessment['id']}/status")

    statements = _capture_statements(do)
    resp = box["resp"]
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"id", "status", "progress"}
    assert body["status"] == "review"
    assert isinstance(body["progress"], dict)

    # The status probe must never join document text or collections.
    for stmt in statements:
        assert "JOIN" not in stmt.upper(), f"status endpoint joined tables: {stmt[:200]}"


def test_status_endpoint_404(client):
    resp = client.get("/api/assessments/999999/status")
    assert resp.status_code == 404


def test_get_assessment_has_no_cartesian_join(client):
    """Regression guard: the assessment GET must not emit the old single
    query that joined every collection at once (12+ JOINs → Postgres temp
    file explosion → DiskFull on small instances)."""
    guideline = make_guideline(client)
    application = make_application(client)
    assessment = make_assessment(client, guideline, application)

    statements = _capture_statements(
        lambda: client.get(f"/api/assessments/{assessment['id']}")
    )
    resp = client.get(f"/api/assessments/{assessment['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == assessment["id"]

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert selects, "expected at least one SELECT"

    # Every statement stays small: collections load via separate "WHERE id IN"
    # selects, so no single SELECT multiplies with collection sizes.
    for stmt in selects:
        join_count = stmt.upper().count("JOIN")
        assert join_count <= 6, (
            f"SELECT has {join_count} JOINs (expected <= 6 constant to-one joins): "
            f"{stmt[:300]}"
        )

    # The root assessments SELECT must never pull in collection tables —
    # that was the Cartesian-product query that filled Postgres temp space.
    root_selects = [s for s in selects if "FROM ASSESSMENTS" in s.upper()]
    assert root_selects, "expected the root SELECT from assessments"
    for stmt in root_selects:
        upper = stmt.upper()
        assert "TEXT_BLOCKS" not in upper, f"collection joined into root query: {stmt[:300]}"
        assert "MAPPING_CITATIONS" not in upper and "SUPPORTING_DOCS" not in upper, (
            f"collections joined into root query: {stmt[:300]}"
        )


def test_llm_logs_endpoint_does_not_load_assessment_graph(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment = make_assessment(client, guideline, application)

    statements = _capture_statements(
        lambda: client.get(f"/api/assessments/{assessment['id']}/llm-logs")
    )
    resp = client.get(f"/api/assessments/{assessment['id']}/llm-logs")
    assert resp.status_code == 200
    assert resp.json() == []

    for stmt in statements:
        assert "JOIN" not in stmt.upper(), f"llm-logs joined tables: {stmt[:200]}"


def test_score_blocks_with_cached_tokenization_matches_fresh():
    blocks = [
        SimpleNamespace(id=1, text="budget must not exceed the ceiling"),
        SimpleNamespace(id=2, text="applicants should include a logic model"),
        SimpleNamespace(id=3, text="signed cover letter required"),
    ]
    query = "budget ceiling"
    fresh = score_blocks(query, blocks, top_k=3)
    cached = score_blocks(query, blocks, top_k=3, tokenized=tokenize_blocks(blocks))
    assert [b.id for _, b in fresh] == [b.id for _, b in cached]
    assert [s for s, _ in fresh] == [s for s, _ in cached]


def test_block_count_warning_appended_over_limit():
    blocks = [BlockData(para_index=i, text=f"p{i}") for i in range(BLOCK_COUNT_SOFT_LIMIT + 1)]
    result = ExtractionResult(blocks=blocks, warnings=["original warning"])
    warnings = _block_count_warnings(result)
    assert "original warning" in warnings
    assert any("soft limit" in w for w in warnings)


def test_block_count_no_warning_under_limit():
    blocks = [BlockData(para_index=i, text="short para") for i in range(10)]
    assert _block_count_warnings(ExtractionResult(blocks=blocks)) == []
