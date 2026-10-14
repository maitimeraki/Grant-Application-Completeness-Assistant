import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  createManualMapping,
  createRequirement,
  fetchAssessment,
  fetchLlmLogs,
  fetchVersionBlocks,
  patchClaim,
  patchMapping,
  patchQuestion,
  patchRequirement,
  patchSupportingDoc,
  rerunAssessment,
} from '../api/client'
import type { Claim, Requirement } from '../api/types'
import { useToast } from '../components/Toast'

type Filter = 'all' | 'mandatory' | 'recommended' | 'ambiguous' | 'missing' | 'unreviewed'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'mandatory', label: 'Mandatory' },
  { key: 'missing', label: 'Missing/weak' },
  { key: 'unreviewed', label: 'Unreviewed' },
  { key: 'recommended', label: 'Recommended' },
  { key: 'ambiguous', label: 'Ambiguous basis' },
]

function matchesFilter(req: Requirement, filter: Filter): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'mandatory':
      return req.basis === 'mandatory'
    case 'recommended':
      return req.basis === 'recommended'
    case 'ambiguous':
      return req.basis === 'ambiguous'
    case 'missing':
      return (
        !req.not_applicable &&
        (!req.mapping ||
          req.mapping.weakness !== null ||
          (req.mapping.status === 'suggested' && req.mapping.citations.length === 0))
      )
    case 'unreviewed':
      return !req.not_applicable && (!req.mapping || req.mapping.status === 'suggested')
  }
}

function BasisBadge({ req }: { req: Requirement }) {
  if (req.not_applicable) return <span className="badge badge-na">N/A</span>
  return <span className={`badge badge-${req.basis}`}>{req.basis}</span>
}

function RequirementList({
  requirements,
  selectedId,
  onSelect,
}: {
  requirements: Requirement[]
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const filtered = requirements.filter((r) => matchesFilter(r, filter))

  return (
    <aside className="req-list pane">
      <div className="filter-row">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={filter === f.key ? 'chip active' : 'chip'}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <ul>
        {filtered.map((r) => (
          <li key={r.id} className={r.id === selectedId ? 'selected' : ''}>
            <button type="button" className="req-item" onClick={() => onSelect(r.id)}>
              <span className="code">{r.code}</span>
              <span className="title">{r.title}</span>
              <span className="meta">
                <BasisBadge req={r} />
                {r.mapping && (
                  <span className={`badge badge-status-${r.mapping.status}`}>{r.mapping.status}</span>
                )}
                {r.mapping?.weakness && <span className="badge badge-weak">{r.mapping.weakness}</span>}
              </span>
            </button>
          </li>
        ))}
        {filtered.length === 0 && <li className="muted pad">No requirements match this filter.</li>}
      </ul>
    </aside>
  )
}

function RequirementDetail({
  assessmentId,
  req,
}: {
  assessmentId: number
  req: Requirement
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] })
  const [note, setNote] = useState(req.mapping?.user_note ?? '')
  const [showCorrect, setShowCorrect] = useState(false)
  const [correctQuote, setCorrectQuote] = useState(req.mapping?.citations[0]?.quote ?? '')
  const [correctBlockId, setCorrectBlockId] = useState<number>(
    req.mapping?.citations[0]?.block_id ?? 0,
  )
  const [actionError, setActionError] = useState('')

  const mappingId = req.mapping?.id

  const reviewMapping = useMutation({
    mutationFn: ({ action }: { action: 'confirm' | 'reject' | 'correct' }) => {
      if (!mappingId) throw new Error('No mapping to review')
      const payload: Parameters<typeof patchMapping>[1] = { action, note }
      if (action === 'correct') {
        if (!correctQuote.trim() || !correctBlockId) throw new Error('Pick a passage to cite')
        payload.quote = correctQuote
        payload.block_id = correctBlockId
      }
      return patchMapping(mappingId, payload)
    },
    onSuccess: (_data, { action }) => {
      setActionError('')
      setShowCorrect(false)
      invalidate()
      const label =
        action === 'confirm' ? 'confirmed' : action === 'reject' ? 'rejected' : 'corrected'
      toast(`Mapping ${label}.`)
    },
    onError: (e: Error) => setActionError(e.message),
  })

  const setNA = useMutation({
    mutationFn: (na: boolean) => patchRequirement(req.id, { not_applicable: na }),
    onSuccess: invalidate,
  })

  const setBasis = useMutation({
    mutationFn: (basis: string) => patchRequirement(req.id, { basis }),
    onSuccess: invalidate,
  })

  return (
    <section className="pane detail">
      <div className="detail-head">
        <h3>
          <span className="code">{req.code}</span> {req.title}
        </h3>
        <div className="row gap">
          <BasisBadge req={req} />
          <select
            value={req.basis}
            onChange={(e) => setBasis.mutate(e.target.value)}
            title="Override mandatory / recommended classification"
          >
            <option value="mandatory">mandatory</option>
            <option value="recommended">recommended</option>
            <option value="ambiguous">ambiguous</option>
          </select>
          <label className="inline">
            <input
              type="checkbox"
              checked={req.not_applicable}
              onChange={(e) => setNA.mutate(e.target.checked)}
            />
            N/A
          </label>
        </div>
      </div>

      <p>{req.requirement_text}</p>
      {req.required_supporting_doc_name && (
        <p className="warn">
          Requires supporting document: <strong>{req.required_supporting_doc_name}</strong>
        </p>
      )}

      <h4>Guideline basis</h4>
      {req.basis_quote ? (
        <blockquote>
          “{req.basis_quote}”
          {!req.basis_verified && (
            <span className="badge badge-weak">quote not found in guideline — basis needs review</span>
          )}
        </blockquote>
      ) : (
        <p className="muted">No source quote recorded.</p>
      )}
      {req.basis_block && (
        <details>
          <summary>Guideline passage</summary>
          <pre className="passage">{req.basis_block.text}</pre>
        </details>
      )}

      <h4>Application evidence</h4>
      {!req.mapping && <p className="muted">No mapping yet — add evidence below or leave for review.</p>}
      {req.mapping && (
        <>
          <p>
            <span className={`badge badge-status-${req.mapping.status}`}>{req.mapping.status}</span>
            {req.mapping.confidence != null && (
              <span className="muted"> confidence {(req.mapping.confidence * 100).toFixed(0)}%</span>
            )}
            {req.mapping.weakness && <span className="badge badge-weak">{req.mapping.weakness}</span>}
          </p>
          {req.mapping.rationale && <p className="muted">{req.mapping.rationale}</p>}
          {req.mapping.citations.map((c) => (
            <div key={c.id} className="citation">
              <blockquote className={c.verified ? '' : 'unverified'}>
                “{c.quote}”
                {c.verified ? (
                  <span className="badge badge-verified">verified</span>
                ) : (
                  <span className="badge badge-weak">not found in application</span>
                )}
              </blockquote>
              {c.block && <pre className="passage">{c.block.text}</pre>}
            </div>
          ))}
        </>
      )}

      {mappingId && (
        <div className="review-actions">
          <label>
            Reviewer note
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
          </label>
          <div className="row gap">
            <button
              className="primary"
              disabled={reviewMapping.isPending}
              onClick={() => reviewMapping.mutate({ action: 'confirm' })}
            >
              Confirm
            </button>
            <button
              className="secondary"
              disabled={reviewMapping.isPending || !req.mapping?.citations.length}
              onClick={() => setShowCorrect(!showCorrect)}
            >
              Correct…
            </button>
            <button
              className="danger"
              disabled={reviewMapping.isPending}
              onClick={() => reviewMapping.mutate({ action: 'reject' })}
            >
              Reject
            </button>
          </div>
          {showCorrect && (
            <CorrectForm
              assessmentId={assessmentId}
              quote={correctQuote}
              setQuote={setCorrectQuote}
              blockId={correctBlockId}
              setBlockId={setCorrectBlockId}
              onSubmit={() => reviewMapping.mutate({ action: 'correct' })}
            />
          )}
          {actionError && <p className="error">{actionError}</p>}
        </div>
      )}
      {!mappingId && <ManualMappingForm assessmentId={assessmentId} requirementId={req.id} />}
    </section>
  )
}

function CorrectForm({
  assessmentId,
  quote,
  setQuote,
  blockId,
  setBlockId,
  onSubmit,
}: {
  assessmentId: number
  quote: string
  setQuote: (v: string) => void
  blockId: number
  setBlockId: (v: number) => void
  onSubmit: () => void
}) {
  const [showPicker, setShowPicker] = useState(false)
  const assessment = useQuery({
    queryKey: ['assessment', assessmentId],
    queryFn: () => fetchAssessment(assessmentId),
  })
  const blocks = useQuery({
    queryKey: ['blocks', assessment.data?.application_version.id],
    queryFn: () => fetchVersionBlocks(assessment.data!.application_version.id),
    enabled: showPicker && !!assessment.data,
  })

  return (
    <div className="correct-form">
      <label>
        Replacement quote (must appear verbatim in the application)
        <textarea rows={2} value={quote} onChange={(e) => setQuote(e.target.value)} />
      </label>
      <button type="button" className="ghost" onClick={() => setShowPicker(!showPicker)}>
        {showPicker ? 'Hide passages' : 'Browse application passages…'}
      </button>
      {showPicker && (
        <div className="passage-picker">
          {blocks.data?.map((b) => (
            <button
              key={b.id}
              type="button"
              className={b.id === blockId ? 'passage-option active' : 'passage-option'}
              onClick={() => {
                setBlockId(b.id)
                setQuote(b.text.slice(0, 240))
              }}
            >
              <small>
                #{b.id}
                {b.page ? ` p.${b.page}` : ''}
                {b.section_path ? ` · ${b.section_path}` : ''}
              </small>
              <pre>{b.text.slice(0, 220)}…</pre>
            </button>
          ))}
        </div>
      )}
      <button className="primary" onClick={onSubmit}>
        Save correction
      </button>
    </div>
  )
}

function ManualMappingForm({
  assessmentId,
  requirementId,
}: {
  assessmentId: number
  requirementId: number
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [quote, setQuote] = useState('')
  const [blockId, setBlockId] = useState<number | null>(null)
  const [error, setError] = useState('')

  const assessment = useQuery({
    queryKey: ['assessment', assessmentId],
    queryFn: () => fetchAssessment(assessmentId),
  })
  const blocks = useQuery({
    queryKey: ['blocks', assessment.data?.application_version.id],
    queryFn: () => fetchVersionBlocks(assessment.data!.application_version.id),
    enabled: !!assessment.data,
  })

  const create = useMutation({
    mutationFn: () => {
      if (!blockId || !quote.trim()) throw new Error('Select a passage and keep its quote')
      return createManualMapping(requirementId, { quote, block_id: blockId, status: 'confirmed' })
    },
    onSuccess: (data) => {
      setError('')
      queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] })
      toast(data.verified ? 'Evidence saved (quote verified).' : 'Evidence saved — quote not found verbatim.')
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="manual-mapping">
      <h4>Add evidence manually</h4>
      <div className="passage-picker">
        {blocks.data?.map((b) => (
          <button
            key={b.id}
            type="button"
            className={b.id === blockId ? 'passage-option active' : 'passage-option'}
            onClick={() => {
              setBlockId(b.id)
              setQuote(b.text.slice(0, 240))
            }}
          >
            <small>
              #{b.id}
              {b.page ? ` p.${b.page}` : ''}
              {b.section_path ? ` · ${b.section_path}` : ''}
            </small>
            <pre>{b.text.slice(0, 200)}…</pre>
          </button>
        ))}
      </div>
      <textarea
        rows={2}
        value={quote}
        onChange={(e) => setQuote(e.target.value)}
        placeholder="Quote to cite (must appear in the selected passage)"
      />
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={create.isPending} onClick={() => create.mutate()}>
        Save mapping
      </button>
    </div>
  )
}

function AddRequirementForm({ assessmentId }: { assessmentId: number }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [basis, setBasis] = useState('mandatory')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () => {
      if (!code.trim() || !title.trim() || !text.trim()) throw new Error('All fields required')
      return createRequirement(assessmentId, {
        code: code.trim(),
        title: title.trim(),
        requirement_text: text.trim(),
        basis,
      })
    },
    onSuccess: () => {
      setOpen(false)
      setCode('')
      setTitle('')
      setText('')
      setError('')
      queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] })
      toast('Requirement added.')
    },
    onError: (e: Error) => setError(e.message),
  })

  if (!open) {
    return (
      <button type="button" className="ghost" onClick={() => setOpen(true)}>
        + Add requirement manually
      </button>
    )
  }
  return (
    <div className="card form-grid">
      <input placeholder="Code (e.g. R10)" value={code} onChange={(e) => setCode(e.target.value)} />
      <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea rows={2} placeholder="Requirement text" value={text} onChange={(e) => setText(e.target.value)} />
      <select value={basis} onChange={(e) => setBasis(e.target.value)}>
        <option value="mandatory">mandatory</option>
        <option value="recommended">recommended</option>
        <option value="ambiguous">ambiguous</option>
      </select>
      {error && <p className="error">{error}</p>}
      <div className="row gap">
        <button className="primary" disabled={create.isPending} onClick={() => create.mutate()}>
          Add
        </button>
        <button className="ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function ClaimItem({ assessmentId, claim }: { assessmentId: number; claim: Claim }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const mutate = useMutation({
    mutationFn: (payload: { user_accepted?: boolean; note?: string }) => patchClaim(claim.id, payload),
    onSuccess: (_data, payload) => {
      queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] })
      toast(payload.user_accepted === true ? 'Claim accepted.' : 'Claim disputed.')
    },
  })
  return (
    <li className="claim">
      <span className={`badge badge-support-${claim.support}`}>{claim.support}</span>
      <p>{claim.claim_text}</p>
      {claim.block && <pre className="passage small">{claim.block.text.slice(0, 200)}</pre>}
      <div className="row gap">
        <button
          className={claim.user_accepted === false ? 'danger active' : 'ghost'}
          onClick={() => mutate.mutate({ user_accepted: false, note: 'disputed by reviewer' })}
        >
          Dispute
        </button>
        <button
          className={claim.user_accepted === true ? 'primary active' : 'ghost'}
          onClick={() => mutate.mutate({ user_accepted: true })}
        >
          Accept
        </button>
      </div>
    </li>
  )
}

function LlmLogSection({ assessmentId, running }: { assessmentId: number; running: boolean }) {
  const { data: logs, isLoading } = useQuery({
    queryKey: ['llm-logs', assessmentId],
    queryFn: () => fetchLlmLogs(assessmentId),
    refetchInterval: running ? 3000 : false,
  })

  if (isLoading) return <p className="muted">Loading AI run log…</p>

  if (!logs || logs.length === 0) {
    return (
      <details className="llm-log">
        <summary>AI run log</summary>
        <p className="muted">
          No AI calls logged yet — this fills in once an AI run starts (manual-review
          assessments have no AI calls).
        </p>
      </details>
    )
  }

  const failures = logs.filter((l) => !l.success).length

  return (
    <details className="llm-log">
      <summary>
        AI run log ({logs.length} call{logs.length === 1 ? '' : 's'}
        {failures > 0 ? `, ${failures} failed` : ''})
      </summary>
      <table className="table">
        <thead>
          <tr>
            <th>Stage</th>
            <th>Model</th>
            <th>#</th>
            <th>OK</th>
            <th>ms</th>
            <th>Tokens</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id} title={l.error ?? undefined}>
              <td>{l.stage}</td>
              <td>
                <code>{l.model}</code>
              </td>
              <td>{l.attempt}</td>
              <td>
                <span className={`badge ${l.success ? 'badge-verified' : 'badge-error'}`}>
                  {l.success ? 'ok' : 'fail'}
                </span>
              </td>
              <td>{l.latency_ms ?? '—'}</td>
              <td>
                {l.tokens_prompt != null && l.tokens_completion != null
                  ? `${l.tokens_prompt}/${l.tokens_completion}`
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {logs.some((l) => l.error) && (
        <ul className="plain">
          {logs
            .filter((l) => l.error)
            .map((l) => (
              <li key={l.id} className="error">
                {l.stage} #{l.attempt}: {l.error}
              </li>
            ))}
        </ul>
      )}
    </details>
  )
}

export default function AssessmentPage() {
  const { id } = useParams()
  const assessmentId = Number(id)
  const queryClient = useQueryClient()
  const toast = useToast()
  const [selectedReqId, setSelectedReqId] = useState<number | null>(null)

  const { data: assessment, error, isLoading } = useQuery({
    queryKey: ['assessment', assessmentId],
    queryFn: () => fetchAssessment(assessmentId),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 2000 : false),
  })

  const rerun = useMutation({
    mutationFn: () => rerunAssessment(assessmentId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['assessment'] })
      toast('Re-run queued against latest versions.')
      window.location.href = `/assessments/${result.id}`
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  const docMutations = {
    togglePresent: useMutation({
      mutationFn: ({ docId, present }: { docId: number; present: boolean }) =>
        patchSupportingDoc(docId, { present }),
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] }),
    }),
    answer: useMutation({
      mutationFn: ({ questionId, answer }: { questionId: number; answer: string }) =>
        patchQuestion(questionId, { answer }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['assessment', assessmentId] })
        toast('Answer saved.')
      },
    }),
  }

  if (isLoading) return <p>Loading…</p>
  if (error) return <p className="error">{(error as Error).message}</p>
  if (!assessment) return null

  if (assessment.status === 'running') {
    return (
      <div className="stack narrow">
        <h2>Assessment #{assessment.id} — running</h2>
        <div className="card">
          <p>
            <span className="badge badge-running">running</span> {String(assessment.progress.stage ?? '')}
          </p>
          <p>{String(assessment.progress.message ?? 'Working…')}</p>
          <p className="muted">This page refreshes automatically.</p>
          <LlmLogSection assessmentId={assessmentId} running />
        </div>
      </div>
    )
  }

  if (assessment.status === 'error') {
    return (
      <div className="stack narrow">
        <h2>Assessment #{assessment.id} — error</h2>
        <p className="error">{String(assessment.progress.message ?? 'Pipeline failed.')}</p>
        <Link to="/new">Create a new assessment (manual review) →</Link>
      </div>
    )
  }

  const requirements = assessment.requirements
  const selected =
    requirements.find((r) => r.id === selectedReqId) ?? requirements[0] ?? null
  const completion = assessment.completion
  const mand = completion.mandatory

  return (
    <div className="stack">
      <div className="assessment-head">
        <div>
          <h2>Assessment #{assessment.id}</h2>
          <p className="muted">
            {assessment.guideline_document_title} v{assessment.guideline_version.version_no} vs{' '}
            {assessment.application_document_title} v{assessment.application_version.version_no}
            {assessment.llm_model ? ` · ${assessment.llm_model}` : ' · manual'}
          </p>
        </div>
        <div className="row gap">
          <Link className="button-link" to={`/assessments/${assessment.id}/summary`}>
            View summary
          </Link>
          <button className="secondary" disabled={rerun.isPending} onClick={() => rerun.mutate()}>
            {rerun.isPending ? 'Re-running…' : 'Re-run against latest versions'}
          </button>
        </div>
      </div>

      {assessment.staleness.stale && (
        <div className="banner stale">
          <strong>Stale assessment.</strong> {assessment.staleness.reasons.join(' ')} Results below
          refer to the older versions.
        </div>
      )}

      <div className="card completion-card">
        <div className="stat">
          <span className="stat-num">{mand.percentage == null ? '—' : `${mand.percentage}%`}</span>
          <span className="muted">mandatory complete</span>
        </div>
        <div className="stat">
          <span className="stat-num">
            {mand.satisfied}/{mand.total}
          </span>
          <span className="muted">satisfied</span>
        </div>
        <div className="stat">
          <span className="stat-num">{mand.open}</span>
          <span className="muted">open</span>
        </div>
        <div className="stat">
          <span className="stat-num">{mand.unsatisfied}</span>
          <span className="muted">unsatisfied</span>
        </div>
        <div className="stat">
          <span className="stat-num">{completion.ambiguous_basis_count}</span>
          <span className="muted">ambiguous basis</span>
        </div>
        <div className="stat">
          <span className="stat-num">
            {completion.recommended.percentage == null ? '—' : `${completion.recommended.percentage}%`}
          </span>
          <span className="muted">recommended complete</span>
        </div>
      </div>

      <div className="workspace">
        <RequirementList
          requirements={requirements}
          selectedId={selected?.id ?? null}
          onSelect={setSelectedReqId}
        />
        {selected ? (
          <RequirementDetail assessmentId={assessmentId} req={selected} />
        ) : (
          <section className="pane detail">
            <p className="muted">No requirements yet.</p>
            <AddRequirementForm assessmentId={assessmentId} />
          </section>
        )}
        <aside className="pane rail">
          <AddRequirementForm assessmentId={assessmentId} />

          <h4>Supporting documents</h4>
          {assessment.supporting_docs.length === 0 && <p className="muted">None declared.</p>}
          <ul className="plain">
            {assessment.supporting_docs.map((d) => (
              <li key={d.id}>
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={d.present}
                    onChange={(e) =>
                      docMutations.togglePresent.mutate({ docId: d.id, present: e.target.checked })
                    }
                  />
                  {d.expected_name}
                  {d.required && <span className="badge badge-mandatory">required</span>}
                </label>
              </li>
            ))}
          </ul>

          <h4>Claims needing evidence</h4>
          {assessment.claims.length === 0 && <p className="muted">No claims flagged.</p>}
          <ul className="plain">
            {assessment.claims.map((c) => (
              <ClaimItem key={c.id} assessmentId={assessmentId} claim={c} />
            ))}
          </ul>

          <h4>Clarification questions</h4>
          {assessment.questions.length === 0 && <p className="muted">No questions generated.</p>}
          <ul className="plain">
            {assessment.questions.map((q) => (
              <li key={q.id} className="question">
                <p>
                  <span className={`badge badge-p${q.priority}`}>P{q.priority}</span> {q.question}
                </p>
                <div className="row gap">
                  <input
                    defaultValue={q.answer ?? ''}
                    placeholder="Answer…"
                    key={`${q.id}-${q.answer ?? ''}`}
                    id={`q-${q.id}`}
                  />
                  <button
                    className="ghost"
                    onClick={() => {
                      const input = document.getElementById(`q-${q.id}`) as HTMLInputElement
                      docMutations.answer.mutate({ questionId: q.id, answer: input.value })
                    }}
                  >
                    Save
                  </button>
                </div>
              </li>
            ))}
          </ul>

          <LlmLogSection assessmentId={assessmentId} running={false} />
        </aside>
      </div>
    </div>
  )
}
