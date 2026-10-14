import type {
  Assessment,
  AssessmentListItem,
  AssessmentStatus,
  Document,
  DocumentVersion,
  LlmCallLog,
  Summary,
  TextBlock,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? detail)
    } catch {
      /* keep statusText */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

function json(payload: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

// ---------- documents ----------

export function fetchDocuments(): Promise<Document[]> {
  return request('/api/documents')
}

export function createDocumentPaste(payload: {
  kind: 'guideline' | 'application'
  title: string
  text: string
}): Promise<Document> {
  return request('/api/documents/paste', json(payload))
}

export function createDocumentUpload(
  kind: 'guideline' | 'application',
  title: string,
  file: File,
): Promise<Document> {
  const form = new FormData()
  form.append('kind', kind)
  form.append('title', title)
  form.append('file', file)
  return request('/api/documents', { method: 'POST', body: form })
}

export function addVersionUpload(documentId: number, file: File): Promise<DocumentVersion> {
  const form = new FormData()
  form.append('file', file)
  return request(`/api/documents/${documentId}/versions`, { method: 'POST', body: form })
}

export function addVersionPaste(documentId: number, text: string): Promise<DocumentVersion> {
  return request(`/api/documents/${documentId}/versions/paste`, json({ text }))
}

export function fetchVersionBlocks(versionId: number): Promise<TextBlock[]> {
  return request(`/api/versions/${versionId}/blocks`)
}

// ---------- assessments ----------

export function fetchAssessments(): Promise<AssessmentListItem[]> {
  return request('/api/assessments')
}

export function fetchAssessment(id: number): Promise<Assessment> {
  return request(`/api/assessments/${id}`)
}

export function fetchAssessmentStatus(id: number): Promise<AssessmentStatus> {
  return request(`/api/assessments/${id}/status`)
}

export function createAssessment(payload: {
  guideline_version_id: number
  application_version_id: number
  supporting_docs: { expected_name: string; required: boolean; present: boolean }[]
  run_llm: boolean
}): Promise<{ id: number; status: string }> {
  return request('/api/assessments', json(payload))
}

export function rerunAssessment(id: number): Promise<{ id: number; status: string }> {
  return request(`/api/assessments/${id}/rerun`, { method: 'POST' })
}

export function fetchSummary(id: number): Promise<Summary> {
  return request(`/api/assessments/${id}/summary`)
}

export function fetchLlmLogs(id: number): Promise<LlmCallLog[]> {
  return request(`/api/assessments/${id}/llm-logs`)
}

// ---------- review actions ----------

export function patchMapping(
  mappingId: number,
  payload: { action: 'confirm' | 'correct' | 'reject'; note?: string; quote?: string; block_id?: number },
): Promise<unknown> {
  return request(`/api/mappings/${mappingId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function patchRequirement(
  requirementId: number,
  payload: { basis?: string; not_applicable?: boolean },
): Promise<unknown> {
  return request(`/api/requirements/${requirementId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function createRequirement(
  assessmentId: number,
  payload: {
    code: string
    title: string
    requirement_text: string
    basis: string
    required_supporting_doc_name?: string
  },
): Promise<{ id: number }> {
  return request(`/api/assessments/${assessmentId}/requirements`, json(payload))
}

export function createManualMapping(
  requirementId: number,
  payload: { quote: string; block_id: number; rationale?: string; status?: 'suggested' | 'confirmed' },
): Promise<{ id: number; verified: boolean }> {
  return request(`/api/requirements/${requirementId}/mappings`, json(payload))
}

export function patchClaim(
  claimId: number,
  payload: { support?: string; note?: string; user_accepted?: boolean },
): Promise<unknown> {
  return request(`/api/claims/${claimId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function patchSupportingDoc(
  docId: number,
  payload: { present?: boolean; required?: boolean },
): Promise<unknown> {
  return request(`/api/supporting-docs/${docId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function patchQuestion(questionId: number, payload: { answer?: string }): Promise<unknown> {
  return request(`/api/questions/${questionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
