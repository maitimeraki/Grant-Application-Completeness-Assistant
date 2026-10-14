from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, DocumentVersion, TextBlock
from app.services.extraction import (
    ExtractionResult,
    content_hash,
    extract_from_file,
    extract_from_text,
)


def _storage_path(version_id: int, filename: str) -> Path:
    settings = get_settings()
    safe_name = Path(filename).name.replace("..", "_")
    directory = settings.storage_dir / "versions" / str(version_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / safe_name


def _persist_version(
    session: Session,
    document: Document,
    extraction: ExtractionResult,
    *,
    data_hash: str,
    source_type: str,
    original_filename: str | None = None,
    raw_bytes: bytes | None = None,
) -> DocumentVersion:
    next_no = max((v.version_no for v in document.versions), default=0) + 1
    version = DocumentVersion(
        document_id=document.id,
        version_no=next_no,
        content_hash=data_hash,
        source_type=source_type,
        original_filename=original_filename,
        extraction_warnings=extraction.warnings,
    )
    session.add(version)
    session.flush()

    if raw_bytes is not None and original_filename:
        version.file_path = str(_storage_path(version.id, original_filename))
        Path(version.file_path).write_bytes(raw_bytes)

    for block in extraction.blocks:
        session.add(
            TextBlock(
                version_id=version.id,
                page=block.page,
                section_path=block.section_path,
                para_index=block.para_index,
                text=block.text,
            )
        )
    session.commit()
    session.refresh(version)
    return version


def create_document_from_upload(
    session: Session, *, kind: str, title: str, filename: str, data: bytes
) -> Document:
    extraction = extract_from_file(filename, data)
    document = Document(kind=kind, title=title.strip())
    session.add(document)
    session.flush()
    _persist_version(
        session,
        document,
        extraction,
        data_hash=content_hash(data),
        source_type="upload",
        original_filename=filename,
        raw_bytes=data,
    )
    session.refresh(document)
    return document


def create_document_from_paste(
    session: Session, *, kind: str, title: str, text: str
) -> Document:
    data = text.encode("utf-8")
    extraction = extract_from_text(data)
    document = Document(kind=kind, title=title.strip())
    session.add(document)
    session.flush()
    _persist_version(
        session,
        document,
        extraction,
        data_hash=content_hash(data, is_text=True),
        source_type="paste",
    )
    session.refresh(document)
    return document


def add_version_from_upload(
    session: Session, document: Document, filename: str, data: bytes
) -> DocumentVersion:
    extraction = extract_from_file(filename, data)
    return _persist_version(
        session,
        document,
        extraction,
        data_hash=content_hash(data),
        source_type="upload",
        original_filename=filename,
        raw_bytes=data,
    )


def add_version_from_paste(session: Session, document: Document, text: str) -> DocumentVersion:
    data = text.encode("utf-8")
    extraction = extract_from_text(data)
    return _persist_version(
        session,
        document,
        extraction,
        data_hash=content_hash(data, is_text=True),
        source_type="paste",
    )
