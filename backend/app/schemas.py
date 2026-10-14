from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- documents ----------

class TextBlockOut(BaseModel):
    id: int
    page: Optional[int] = None
    section_path: Optional[str] = None
    para_index: int
    text: str

    model_config = {"from_attributes": True}


class DocumentVersionOut(BaseModel):
    id: int
    version_no: int
    content_hash: str
    source_type: str
    original_filename: Optional[str] = None
    extraction_warnings: list[str] = []
    created_at: dt.datetime
    block_count: int = 0

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: int
    kind: str
    title: str
    created_at: dt.datetime
    versions: list[DocumentVersionOut] = []

    model_config = {"from_attributes": True}


class DocumentPasteIn(BaseModel):
    kind: Literal["guideline", "application"]
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)


class DocumentVersionPasteIn(BaseModel):
    text: str = Field(min_length=1)


# ---------- assessments ----------

class SupportingDocIn(BaseModel):
    expected_name: str = Field(min_length=1, max_length=200)
    required: bool = False
    present: bool = False
    metadata: dict = Field(default_factory=dict)


class AssessmentCreate(BaseModel):
    guideline_version_id: int
    application_version_id: int
    supporting_docs: list[SupportingDocIn] = Field(default_factory=list)
    run_llm: bool = True


class SupportingDocOut(BaseModel):
    id: int
    expected_name: str
    required: bool
    present: bool
    metadata_json: dict
    source: str

    model_config = {"from_attributes": True}


class CitationOut(BaseModel):
    id: int
    block_id: int
    quote: str
    verified: bool
    block: Optional[TextBlockOut] = None

    model_config = {"from_attributes": True}


class MappingOut(BaseModel):
    id: int
    status: str
    rationale: Optional[str] = None
    confidence: Optional[float] = None
    weakness: Optional[str] = None
    user_note: Optional[str] = None
    source: str
    reviewed_at: Optional[dt.datetime] = None
    citations: list[CitationOut] = []

    model_config = {"from_attributes": True}


class RequirementOut(BaseModel):
    id: int
    code: str
    title: str
    requirement_text: str
    basis: str
    basis_quote: Optional[str] = None
    basis_block_id: Optional[int] = None
    basis_verified: bool = False
    basis_block: Optional[TextBlockOut] = None
    category: Optional[str] = None
    required_supporting_doc_name: Optional[str] = None
    not_applicable: bool = False
    source: str
    mapping: Optional[MappingOut] = None

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    id: int
    block_id: Optional[int] = None
    claim_text: str
    support: str
    supporting_refs: list[str] = []
    note: Optional[str] = None
    user_accepted: Optional[bool] = None
    source: str
    block: Optional[TextBlockOut] = None

    model_config = {"from_attributes": True}


class QuestionOut(BaseModel):
    id: int
    requirement_id: Optional[int] = None
    question: str
    priority: int
    answer: Optional[str] = None
    answered_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class AssessmentOut(BaseModel):
    id: int
    status: str
    progress: dict
    pipeline_version: str
    llm_model: Optional[str] = None
    created_at: dt.datetime
    completed_at: Optional[dt.datetime] = None
    guideline_version: DocumentVersionOut
    application_version: DocumentVersionOut
    guideline_document_title: str = ""
    application_document_title: str = ""
    requirements: list[RequirementOut] = []
    supporting_docs: list[SupportingDocOut] = []
    claims: list[ClaimOut] = []
    questions: list[QuestionOut] = []
    completion: dict = {}
    staleness: dict = {}


class AssessmentSummaryOut(BaseModel):
    assessment_id: int
    markdown: str
    disclaimer: str


class LLMCallLogOut(BaseModel):
    id: int
    stage: str
    model: str
    attempt: int
    success: bool
    error: Optional[str] = None
    latency_ms: Optional[int] = None
    prompt_chars: Optional[int] = None
    completion_chars: Optional[int] = None
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ---------- review actions ----------

class MappingPatch(BaseModel):
    action: Literal["confirm", "correct", "reject"]
    note: Optional[str] = None
    quote: Optional[str] = None
    block_id: Optional[int] = None


class RequirementPatch(BaseModel):
    basis: Optional[Literal["mandatory", "recommended", "ambiguous"]] = None
    not_applicable: Optional[bool] = None


class ClaimPatch(BaseModel):
    support: Optional[Literal["supported", "partial", "unsupported"]] = None
    note: Optional[str] = None
    user_accepted: Optional[bool] = None


class SupportingDocPatch(BaseModel):
    present: Optional[bool] = None
    required: Optional[bool] = None


class QuestionPatch(BaseModel):
    answer: Optional[str] = None


class RequirementCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    requirement_text: str = Field(min_length=1)
    basis: Literal["mandatory", "recommended", "ambiguous"] = "ambiguous"
    basis_quote: Optional[str] = None
    basis_block_id: Optional[int] = None
    category: Optional[str] = None
    required_supporting_doc_name: Optional[str] = None


class MappingCreate(BaseModel):
    rationale: Optional[str] = None
    quote: str = Field(min_length=1)
    block_id: int
    status: Literal["suggested", "confirmed"] = "confirmed"
