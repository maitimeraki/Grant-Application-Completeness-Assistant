from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # "guideline" | "application"
    title: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document",
        order_by="DocumentVersion.version_no",
        cascade="all, delete-orphan",
    )

    @property
    def active_version(self) -> Optional[DocumentVersion]:
        return self.versions[-1] if self.versions else None


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(20))  # "upload" | "paste"
    original_filename: Mapped[Optional[str]] = mapped_column(String(300))
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    extraction_warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="versions")
    blocks: Mapped[list[TextBlock]] = relationship(
        back_populates="version",
        order_by="TextBlock.para_index",
        cascade="all, delete-orphan",
    )


class TextBlock(Base):
    __tablename__ = "text_blocks"
    __table_args__ = (UniqueConstraint("version_id", "para_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    para_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    version: Mapped[DocumentVersion] = relationship(back_populates="blocks")


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guideline_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    application_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    guideline_hash: Mapped[str] = mapped_column(String(64))
    application_hash: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(20), default="1")
    llm_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|review|error
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    guideline_version: Mapped[DocumentVersion] = relationship(foreign_keys=[guideline_version_id])
    application_version: Mapped[DocumentVersion] = relationship(foreign_keys=[application_version_id])
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="Requirement.code"
    )
    supporting_docs: Mapped[list[SupportingDoc]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    claims: Mapped[list[Claim]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    questions: Mapped[list[ClarificationQuestion]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("assessment_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    requirement_text: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(String(20), default="ambiguous")  # mandatory|recommended|ambiguous
    basis_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    basis_block_id: Mapped[Optional[int]] = mapped_column(ForeignKey("text_blocks.id"), nullable=True)
    basis_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    required_supporting_doc_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    not_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="llm")  # llm|manual

    assessment: Mapped[Assessment] = relationship(back_populates="requirements")
    basis_block: Mapped[Optional[TextBlock]] = relationship(foreign_keys=[basis_block_id])
    mapping: Mapped[Optional[Mapping]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", uselist=False
    )


class Mapping(Base):
    __tablename__ = "mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="suggested")  # suggested|confirmed|corrected|rejected
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weakness: Mapped[Optional[str]] = mapped_column(String(20), nullable=True) # missing|weak|ambiguous|None
    user_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="llm")
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    requirement: Mapped[Requirement] = relationship(back_populates="mapping")
    citations: Mapped[list[MappingCitation]] = relationship(
        back_populates="mapping", cascade="all, delete-orphan"
    )


class MappingCitation(Base):
    __tablename__ = "mapping_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mapping_id: Mapped[int] = mapped_column(ForeignKey("mappings.id"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("text_blocks.id"))
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    mapping: Mapped[Mapping] = relationship(back_populates="citations")
    block: Mapped[TextBlock] = relationship()


class SupportingDoc(Base):
    __tablename__ = "supporting_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    expected_name: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user|llm

    assessment: Mapped[Assessment] = relationship(back_populates="supporting_docs")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    block_id: Mapped[Optional[int]] = mapped_column(ForeignKey("text_blocks.id"), nullable=True)
    claim_text: Mapped[str] = mapped_column(Text)
    support: Mapped[str] = mapped_column(String(20), default="unsupported")  # supported|partial|unsupported
    supporting_refs: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_accepted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="llm")

    assessment: Mapped[Assessment] = relationship(back_populates="claims")
    block: Mapped[Optional[TextBlock]] = relationship()


class ClarificationQuestion(Base):
    __tablename__ = "clarification_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    requirement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requirements.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answered_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="questions")


class LLMCallLog(Base):
    """Structured record of every LLM call made by the AI pipeline."""

    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessments.id"), nullable=True, index=True
    )
    stage: Mapped[str] = mapped_column(String(50))  # extract_requirements|map_requirements|claims|questions|...
    model: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_prompt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessment: Mapped[Optional[Assessment]] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
