import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchAssessments } from '../api/client'

export default function AssessmentListRoute() {
  const { data, error, isLoading } = useQuery({
    queryKey: ['assessments'],
    queryFn: fetchAssessments,
    refetchInterval: (query) =>
      query.state.error
        ? false
        : query.state.data?.some((a) => a.status === 'running')
          ? 10000
          : false,
  })

  if (isLoading) return <p>Loading…</p>
  if (error) return <p className="error">{(error as Error).message}</p>

  return (
    <div className="stack">
      <h2>Assessments</h2>
      {data?.length === 0 && (
        <p className="muted">
          No assessments yet. <Link to="/new">Create one →</Link>
        </p>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>#</th>
            <th>Status</th>
            <th>Guideline</th>
            <th>Application</th>
            <th>Created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((a) => (
            <tr key={a.id}>
              <td>{a.id}</td>
              <td>
                <span className={`badge badge-${a.status}`}>{a.status}</span>
              </td>
              <td>
                {a.guideline_title} <span className="muted">v{a.guideline_version_no}</span>
              </td>
              <td>
                {a.application_title} <span className="muted">v{a.application_version_no}</span>
              </td>
              <td>{new Date(a.created_at).toLocaleString()}</td>
              <td>
                <Link to={`/assessments/${a.id}`}>Open →</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
