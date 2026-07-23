import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './app/App';
import { AuthProvider } from './features/auth/AuthContext';
import { AnalysisProvider } from './features/analysis/AnalysisContext';
import './styles/global.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <AnalysisProvider><App /></AnalysisProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
