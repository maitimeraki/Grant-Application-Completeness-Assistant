import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { fetchSummary } from '../api/client'

export default function SummaryPage() {
  const { id } = useParams()
  const assessmentId = Number(id)
  const { data, error, isLoading } = useQuery({
    queryKey: ['summary', assessmentId],
    queryFn: () => fetchSummary(assessmentId),
  })

  if (isLoading) return <p>Loading…</p>
  if (error) return <p className="error">{(error as Error).message}</p>
  if (!data) return null

  const download = () => {
    const blob = new Blob([data.markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `completeness-summary-assessment-${assessmentId}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="stack">
      <div className="assessment-head">
        <h2>Reviewed completeness summary</h2>
        <div className="row gap">
          <Link className="button-link" to={`/assessments/${assessmentId}`}>
            ← Back to workspace
          </Link>
          <button className="secondary" onClick={download}>
            Download .md
          </button>
          <button className="ghost" onClick={() => window.print()}>
            Print
          </button>
        </div>
      </div>
      <div className="banner stale">{data.disclaimer}</div>
      <pre className="summary-markdown">{data.markdown}</pre>
    </div>
  )
}
