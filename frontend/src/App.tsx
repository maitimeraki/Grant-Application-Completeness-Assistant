import { Link, NavLink, Route, Routes } from 'react-router-dom'
import AssessmentPage from './pages/AssessmentPage'
import AssessmentListRoute from './pages/AssessmentListPage'
import DocumentsPage from './pages/DocumentsPage'
import NewAssessmentPage from './pages/NewAssessmentPage'
import SummaryPage from './pages/SummaryPage'

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand">
          Grant Application Completeness Assistant
        </Link>
        <nav>
          <NavLink to="/" end>
            Documents
          </NavLink>
          <NavLink to="/assessments">Assessments</NavLink>
          <NavLink to="/new">New assessment</NavLink>
        </nav>
      </header>
      <div className="app-content">
        <Routes>
          <Route path="/" element={<DocumentsPage />} />
          <Route path="/assessments" element={<AssessmentListRoute />} />
          <Route path="/new" element={<NewAssessmentPage />} />
          <Route path="/assessments/:id" element={<AssessmentPage />} />
          <Route path="/assessments/:id/summary" element={<SummaryPage />} />
        </Routes>
      </div>
      <footer className="footer disclaimer">
        AI-assisted completeness review only — not an authoritative
        funding-eligibility or legal determination.
      </footer>
    </div>
  )
}
