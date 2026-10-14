from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import Document, DocumentVersion, TextBlock
from app.schemas import (
    DocumentOut,
    DocumentPasteIn,
    DocumentVersionOut,
    DocumentVersionPasteIn,
    TextBlockOut,
)
from app.services import documents as doc_service

router = APIRouter(prefix="/api", tags=["documents"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _version_out(version: DocumentVersion) -> DocumentVersionOut:
    return DocumentVersionOut(
        id=version.id,
        version_no=version.version_no,
        content_hash=version.content_hash,
        source_type=version.source_type,
        original_filename=version.original_filename,
        extraction_warnings=version.extraction_warnings or [],
        created_at=version.created_at,
        block_count=len(version.blocks),
    )


def _doc_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        kind=document.kind,
        title=document.title,
        created_at=document.created_at,
        versions=[_version_out(v) for v in document.versions],
    )


def _get_document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(
        Document,
        document_id,
        options=[joinedload(Document.versions)],
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MB limit")
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    return data


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)) -> list[DocumentOut]:
    docs = db.scalars(
        select(Document).options(joinedload(Document.versions)).order_by(Document.created_at.desc())
    ).unique().all()
    return [_doc_out(d) for d in docs]


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def create_document_upload(
    kind: str = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentOut:
    if kind not in ("guideline", "application"):
        raise HTTPException(status_code=400, detail="kind must be 'guideline' or 'application'")
    data = await _read_upload(file)
    try:
        document = doc_service.create_document_from_upload(
            db, kind=kind, title=title, filename=file.filename or "upload", data=data
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _doc_out(_get_document_or_404(db, document.id))


@router.post("/documents/paste", response_model=DocumentOut, status_code=201)
def create_document_paste(body: DocumentPasteIn, db: Session = Depends(get_db)) -> DocumentOut:
    document = doc_service.create_document_from_paste(
        db, kind=body.kind, title=body.title, text=body.text
    )
    return _doc_out(_get_document_or_404(db, document.id))


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)) -> DocumentOut:
    return _doc_out(_get_document_or_404(db, document_id))


@router.post(
    "/documents/{document_id}/versions", response_model=DocumentVersionOut, status_code=201
)
async def add_version_upload(
    document_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentVersionOut:
    document = _get_document_or_404(db, document_id)
    data = await _read_upload(file)
    try:
        version = doc_service.add_version_from_upload(
            db, document, file.filename or "upload", data
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _version_out(version)


@router.post(
    "/documents/{document_id}/versions/paste",
    response_model=DocumentVersionOut,
    status_code=201,
)
def add_version_paste(
    document_id: int, body: DocumentVersionPasteIn, db: Session = Depends(get_db)
) -> DocumentVersionOut:
    document = _get_document_or_404(db, document_id)
    version = doc_service.add_version_from_paste(db, document, body.text)
    return _version_out(version)


@router.get("/versions/{version_id}", response_model=DocumentVersionOut)
def get_version(version_id: int, db: Session = Depends(get_db)) -> DocumentVersionOut:
    version = db.get(
        DocumentVersion, version_id, options=[joinedload(DocumentVersion.blocks)]
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(version)


@router.get("/versions/{version_id}/blocks", response_model=list[TextBlockOut])
def list_version_blocks(version_id: int, db: Session = Depends(get_db)) -> list[TextBlockOut]:
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    blocks = db.scalars(
        select(TextBlock).where(TextBlock.version_id == version_id).order_by(TextBlock.para_index)
    ).all()
    return [TextBlockOut.model_validate(b) for b in blocks]


@router.get("/blocks/{block_id}", response_model=TextBlockOut)
def get_block(block_id: int, db: Session = Depends(get_db)) -> TextBlockOut:
    block = db.get(TextBlock, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return TextBlockOut.model_validate(block)
