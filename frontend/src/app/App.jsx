import { Navigate, Route, Routes } from 'react-router-dom';
import { RequireAuth } from './RequireAuth';
import { AppLayout } from '../components/layout/AppLayout';
import { LandingPage } from '../pages/LandingPage';
import { AnalysisPage } from '../pages/AnalysisPage';
import { ResultsPage } from '../pages/ResultsPage';
import { AuthPage } from '../pages/AuthPage';
import { DashboardPage } from '../pages/app/DashboardPage';
import { ProjectsPage } from '../pages/app/ProjectsPage';
import { HistoryPage } from '../pages/app/HistoryPage';
import { ComparePage } from '../pages/app/ComparePage';
import { ProfilePage } from '../pages/app/ProfilePage';

export default function App() {
  return <Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/analysis" element={<AnalysisPage />} />
    <Route path="/results" element={<ResultsPage />} />
    <Route path="/auth" element={<AuthPage />} />
    <Route path="/app" element={<RequireAuth><AppLayout /></RequireAuth>}>
      <Route index element={<DashboardPage />} />
      <Route path="projects" element={<ProjectsPage />} />
      <Route path="history" element={<HistoryPage />} />
      <Route path="compare/:baseId/:headId" element={<ComparePage />} />
      <Route path="profile" element={<ProfilePage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>;
}
