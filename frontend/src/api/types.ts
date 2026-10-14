export interface TextBlock {
  id: number
  page: number | null
  section_path: string | null
  para_index: number
  text: string
}

export interface DocumentVersion {
  id: number
  version_no: number
  content_hash: string
  source_type: string
  original_filename: string | null
  extraction_warnings: string[]
  created_at: string
  block_count: number
}

export interface Document {
  id: number
  kind: string
  title: string
  created_at: string
  versions: DocumentVersion[]
}

export interface Citation {
  id: number
  block_id: number
  quote: string
  verified: boolean
  block: TextBlock | null
}

export interface Mapping {
  id: number
  status: 'suggested' | 'confirmed' | 'corrected' | 'rejected'
  rationale: string | null
  confidence: number | null
  weakness: string | null
  user_note: string | null
  source: string
  reviewed_at: string | null
  citations: Citation[]
}

export interface Requirement {
  id: number
  code: string
  title: string
  requirement_text: string
  basis: 'mandatory' | 'recommended' | 'ambiguous'
  basis_quote: string | null
  basis_block_id: number | null
  basis_verified: boolean
  basis_block: TextBlock | null
  category: string | null
  required_supporting_doc_name: string | null
  not_applicable: boolean
  source: string
  mapping: Mapping | null
}

export interface SupportingDoc {
  id: number
  expected_name: string
  required: boolean
  present: boolean
  metadata_json: Record<string, unknown>
  source: string
}

export interface Claim {
  id: number
  block_id: number | null
  claim_text: string
  support: 'supported' | 'partial' | 'unsupported'
  supporting_refs: string[]
  note: string | null
  user_accepted: boolean | null
  source: string
  block: TextBlock | null
}

export interface Question {
  id: number
  requirement_id: number | null
  question: string
  priority: number
  answer: string | null
  answered_at: string | null
}

export interface CompletionBucket {
  total: number
  satisfied: number
  open: number
  unsatisfied: number
  not_applicable: number
  percentage: number | null
  open_items: { code: string; title: string; weakness?: string }[]
  unsatisfied_items: { code: string; title: string; reason: string }[]
}

export interface Completion {
  disclaimer: string
  mandatory: CompletionBucket
  recommended: CompletionBucket
  ambiguous_basis_count: number
  attention: {
    weak_or_ambiguous_mappings: number
    confirmed_without_verified_citation: number
  }
}

export interface Staleness {
  stale: boolean
  guideline_stale: boolean
  application_stale: boolean
  reasons: string[]
}

export interface Assessment {
  id: number
  status: 'running' | 'review' | 'error'
  progress: { stage?: string; message?: string; [k: string]: unknown }
  pipeline_version: string
  llm_model: string | null
  created_at: string
  completed_at: string | null
  guideline_version: DocumentVersion
  application_version: DocumentVersion
  guideline_document_title: string
  application_document_title: string
  requirements: Requirement[]
  supporting_docs: SupportingDoc[]
  claims: Claim[]
  questions: Question[]
  completion: Completion
  staleness: Staleness
}

export interface AssessmentListItem {
  id: number
  status: string
  created_at: string
  guideline_title: string
  guideline_version_no: number
  application_title: string
  application_version_no: number
}

export interface Summary {
  assessment_id: number
  markdown: string
  disclaimer: string
}

export interface LlmCallLog {
  id: number
  stage: string
  model: string
  attempt: number
  success: boolean
  error: string | null
  latency_ms: number | null
  prompt_chars: number | null
  completion_chars: number | null
  tokens_prompt: number | null
  tokens_completion: number | null
  created_at: string
}
