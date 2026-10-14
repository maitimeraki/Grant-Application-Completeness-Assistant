import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  addVersionPaste,
  addVersionUpload,
  createDocumentPaste,
  createDocumentUpload,
  fetchDocuments,
} from '../api/client'
import type { Document } from '../api/types'
import { useToast } from '../components/Toast'

function VersionRow({ version }: { version: Document['versions'][number] }) {
  return (
    <li className="version-row">
      <span className="badge">v{version.version_no}</span>
      <code title={version.content_hash}>{version.content_hash.slice(0, 10)}</code>
      <span className="muted">
        {version.source_type} · {version.block_count} blocks
        {version.original_filename ? ` · ${version.original_filename}` : ''}
      </span>
      {version.extraction_warnings.map((w) => (
        <span key={w} className="warn">
          {w}
        </span>
      ))}
    </li>
  )
}

function DocumentCard({ doc }: { doc: Document }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [file, setFile] = useState<File | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [mode, setMode] = useState<'upload' | 'paste'>('upload')
  const [error, setError] = useState('')

  const addVersion = useMutation({
    mutationFn: () => {
      if (mode === 'upload') {
        if (!file) throw new Error('Choose a file first')
        return addVersionUpload(doc.id, file)
      }
      if (!pasteText.trim()) throw new Error('Paste some text first')
      return addVersionPaste(doc.id, pasteText)
    },
    onSuccess: (version) => {
      setFile(null)
      setPasteText('')
      setError('')
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      toast(`Version ${version.version_no} added to “${doc.title}”.`)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <section className="card">
      <div className="card-head">
        <h3>{doc.title}</h3>
        <span className={`badge badge-${doc.kind}`}>{doc.kind}</span>
      </div>
      <ul className="version-list">
        {doc.versions.map((v) => (
          <VersionRow key={v.id} version={v} />
        ))}
      </ul>

      <div className="row gap">
        <button
          className={mode === 'upload' ? 'tab active' : 'tab'}
          onClick={() => setMode('upload')}
          type="button"
        >
          Upload new version
        </button>
        <button
          className={mode === 'paste' ? 'tab active' : 'tab'}
          onClick={() => setMode('paste')}
          type="button"
        >
          Paste new version
        </button>
      </div>
      {mode === 'upload' ? (
        <input type="file" accept=".pdf,.docx,.md,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      ) : (
        <textarea
          rows={4}
          placeholder="Paste the full document text…"
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
        />
      )}
      {error && <p className="error">{error}</p>}
      <button
        className="primary"
        disabled={addVersion.isPending}
        onClick={() => addVersion.mutate()}
        type="button"
      >
        {addVersion.isPending ? 'Adding…' : 'Add version'}
      </button>
    </section>
  )
}

export default function DocumentsPage() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const { data: documents, error, isLoading } = useQuery({ queryKey: ['documents'], queryFn: fetchDocuments })

  const [kind, setKind] = useState<'guideline' | 'application'>('guideline')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [mode, setMode] = useState<'upload' | 'paste'>('upload')
  const [formError, setFormError] = useState('')

  const create = useMutation({
    mutationFn: () => {
      if (!title.trim()) throw new Error('Title is required')
      if (mode === 'upload') {
        if (!file) throw new Error('Choose a file first')
        return createDocumentUpload(kind, title, file)
      }
      if (!pasteText.trim()) throw new Error('Paste some text first')
      return createDocumentPaste({ kind, title, text: pasteText })
    },
    onSuccess: (created) => {
      setTitle('')
      setFile(null)
      setPasteText('')
      setFormError('')
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      toast(`Document “${created.title}” added.`)
    },
    onError: (e: Error) => setFormError(e.message),
  })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }

  const guidelines = documents?.filter((d) => d.kind === 'guideline') ?? []
  const applications = documents?.filter((d) => d.kind === 'application') ?? []
  const samplesLoaded = (documents ?? []).some((d) => d.title.startsWith('[Sample'))

  return (
    <div className="stack">
      <h2>Documents</h2>
      <p className="muted">
        Upload or paste the grant guideline and the draft application. Every edit creates a new
        immutable version; existing assessments are marked stale when a new version appears.
      </p>

      {samplesLoaded && (
        <section className="card">
          <h3>Sample data loaded</h3>
          <p className="muted">
            Two sample sets are pre-loaded so you can review the full workflow immediately:
          </p>
          <ul>
            <li>
              <strong>Sample A — worked example.</strong> The ClosedLoop-Dye draft has been
              through the AI pipeline and a human-review pass. Open it from the{' '}
              <Link to="/assessments">Assessments</Link> page to see cited mappings
              (confirmed / corrected / rejected), missing documents, unsupported claims,
              answered clarification questions, the deterministic checklist, and the AI run log.
            </li>
            <li>
              <strong>Sample B — test it yourself.</strong> An intentionally weaker draft
              (AquaBright). Open its assessment and click{' '}
              <em>“Re-run against latest versions”</em> to run a fresh AI review end-to-end
              against this deployment.
            </li>
          </ul>
        </section>
      )}

      <section className="card">
        <h3>Add a document</h3>
        <form className="form-grid" onSubmit={onSubmit}>
          <label>
            Kind
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="guideline">Grant guideline</option>
              <option value="application">Draft application</option>
            </select>
          </label>
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Community Grant 2027" />
          </label>
          <div className="row gap">
            <button className={mode === 'upload' ? 'tab active' : 'tab'} type="button" onClick={() => setMode('upload')}>
              Upload file
            </button>
            <button className={mode === 'paste' ? 'tab active' : 'tab'} type="button" onClick={() => setMode('paste')}>
              Paste text
            </button>
          </div>
          {mode === 'upload' ? (
            <input type="file" accept=".pdf,.docx,.md,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          ) : (
            <textarea
              rows={6}
              placeholder="Paste the full document text…"
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
          )}
          {formError && <p className="error">{formError}</p>}
          <button className="primary" type="submit" disabled={create.isPending}>
            {create.isPending ? 'Adding…' : 'Add document'}
          </button>
        </form>
      </section>

      {isLoading && <p>Loading…</p>}
      {error && <p className="error">{(error as Error).message}</p>}

      <h3>Grant guidelines</h3>
      {guidelines.length === 0 && <p className="muted">None yet.</p>}
      <div className="grid-2">
        {guidelines.map((d) => (
          <DocumentCard key={d.id} doc={d} />
        ))}
      </div>

      <h3>Draft applications</h3>
      {applications.length === 0 && <p className="muted">None yet.</p>}
      <div className="grid-2">
        {applications.map((d) => (
          <DocumentCard key={d.id} doc={d} />
        ))}
      </div>

      <p>
        Ready? <Link to="/new">Create a new assessment →</Link>
      </p>
    </div>
  )
}
