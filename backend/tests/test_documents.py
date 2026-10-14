from __future__ import annotations

from tests.conftest import GUIDELINE_MD, make_application, make_guideline


def test_paste_creates_document_with_blocks(client):
    doc = make_guideline(client)
    assert doc["kind"] == "guideline"
    assert len(doc["versions"]) == 1
    version = doc["versions"][0]
    assert version["version_no"] == 1
    assert len(version["content_hash"]) == 64
    assert version["block_count"] > 0
    assert version["extraction_warnings"] == []

    blocks = client.get(f"/api/versions/{version['id']}/blocks").json()
    joined = "\n".join(b["text"] for b in blocks)
    assert "registered non-profit" in joined
    assert "50,000 EUR" in joined
    # section paths were assigned from markdown headings
    assert any(b["section_path"] for b in blocks)


def test_new_version_increments_and_keeps_history(client):
    doc = make_guideline(client)
    resp = client.post(
        f"/api/documents/{doc['id']}/versions/paste",
        json={"text": GUIDELINE_MD + "\n\nNew deadline: 1 March 2027.\n"},
    )
    assert resp.status_code == 201
    assert resp.json()["version_no"] == 2

    full = client.get(f"/api/documents/{doc['id']}").json()
    assert [v["version_no"] for v in full["versions"]] == [1, 2]
    assert full["versions"][0]["content_hash"] != full["versions"][1]["content_hash"]


def test_upload_txt(client):
    resp = client.post(
        "/api/documents",
        data={"kind": "application", "title": "Uploaded"},
        files={"file": ("app.txt", b"# Hello\n\nWorld.", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["versions"][0]["source_type"] == "upload"
    assert doc["versions"][0]["original_filename"] == "app.txt"


def test_unsupported_file_type_rejected(client):
    resp = client.post(
        "/api/documents",
        data={"kind": "application", "title": "Bad"},
        files={"file": ("app.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_empty_paste_rejected(client):
    resp = client.post(
        "/api/documents/paste",
        json={"kind": "guideline", "title": "Empty", "text": ""},
    )
    assert resp.status_code == 422


def test_application_listed(client):
    make_application(client)
    docs = client.get("/api/documents").json()
    assert any(d["kind"] == "application" for d in docs)
