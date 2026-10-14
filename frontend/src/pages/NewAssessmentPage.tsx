import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createAssessment, fetchDocuments } from '../api/client'
import type { Document } from '../api/types'
import { useToast } from '../components/Toast'

interface DocRow {
  name: string
  required: boolean
}

export default function NewAssessmentPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const { data: documents, error, isLoading } = useQuery({ queryKey: ['documents'], queryFn: fetchDocuments })

  const guidelines = (documents ?? []).filter((d) => d.kind === 'guideline')
  const applications = (documents ?? []).filter((d) => d.kind === 'application')

  const latestVersionId = (doc: Document | undefined) => doc?.versions[doc.versions.length - 1]?.id ?? 0

  const [guidelineId, setGuidelineId] = useState<number>(0)
  const [applicationId, setApplicationId] = useState<number>(0)
  const [docs, setDocs] = useState<DocRow[]>([])
  const [runLlm, setRunLlm] = useState(true)
  const [formError, setFormError] = useState('')

  const create = useMutation({
    mutationFn: () => {
      const gVersion = guidelines.find((d) => d.id === guidelineId)
      const aVersion = applications.find((d) => d.id === applicationId)
      if (!gVersion || !latestVersionId(gVersion)) throw new Error('Select a grant guideline')
      if (!aVersion || !latestVersionId(aVersion)) throw new Error('Select a draft application')
      return createAssessment({
        guideline_version_id: latestVersionId(gVersion),
        application_version_id: latestVersionId(aVersion),
        supporting_docs: docs
          .filter((d) => d.name.trim())
          .map((d) => ({ expected_name: d.name.trim(), required: d.required, present: false })),
        run_llm: runLlm,
      })
    },
    onSuccess: (result) => {
      toast(result.status === 'running' ? 'Assessment queued — AI review running.' : 'Assessment created.')
      navigate(`/assessments/${result.id}`)
    },
    onError: (e: Error) => setFormError(e.message),
  })

  if (isLoading) return <p>Loading…</p>

  return (
    <div className="stack narrow">
      <h2>New assessment</h2>
      <p className="muted">
        The latest version of each selected document is used. With “run AI review” the pipeline
        extracts requirements, maps them to the application, and flags gaps — every mapping still
        needs your confirmation.
      </p>
      {error && <p className="error">{(error as Error).message}</p>}
      {(guidelines.length === 0 || applications.length === 0) && (
        <p className="warn">You need at least one guideline and one draft application — add them on the Documents page first.</p>
      )}

      <section className="card form-grid">
        <label>
          Grant guideline
          <select value={guidelineId || ''} onChange={(e) => setGuidelineId(Number(e.target.value))}>
            <option value="">Select…</option>
            {guidelines.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title} (v{d.versions[d.versions.length - 1]?.version_no})
              </option>
            ))}
          </select>
        </label>
        <label>
          Draft application
          <select value={applicationId || ''} onChange={(e) => setApplicationId(Number(e.target.value))}>
            <option value="">Select…</option>
            {applications.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title} (v{d.versions[d.versions.length - 1]?.version_no})
              </option>
            ))}
          </select>
        </label>

        <h4>Supporting documents (optional metadata)</h4>
        {docs.map((row, i) => (
          <div className="row gap" key={i}>
            <input
              placeholder="e.g. Signed cover letter"
              value={row.name}
              onChange={(e) =>
                setDocs(docs.map((d, j) => (j === i ? { ...d, name: e.target.value } : d)))
              }
            />
            <label className="inline">
              <input
                type="checkbox"
                checked={row.required}
                onChange={(e) =>
                  setDocs(docs.map((d, j) => (j === i ? { ...d, required: e.target.checked } : d)))
                }
              />
              required
            </label>
            <button
              type="button"
              className="ghost"
              onClick={() => setDocs(docs.filter((_, j) => j !== i))}
            >
              remove
            </button>
          </div>
        ))}
        <button type="button" className="ghost" onClick={() => setDocs([...docs, { name: '', required: true }])}>
          + Add supporting document
        </button>

        <label className="inline">
          <input type="checkbox" checked={runLlm} onChange={(e) => setRunLlm(e.target.checked)} />
          Run AI review (requires LLM API key in backend/.env)
        </label>

        {formError && <p className="error">{formError}</p>}
        <button className="primary" disabled={create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? 'Creating…' : 'Create assessment'}
        </button>
      </section>
    </div>
  )
}
