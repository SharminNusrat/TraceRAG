import { createContext, useContext, useMemo, useState } from 'react';
import { defaultAnalysisDraft } from './analysisDefaults';

const AnalysisContext = createContext(null);

export function AnalysisProvider({ children }) {
  const [draft, setDraft] = useState(defaultAnalysisDraft);
  const [result, setResult] = useState(null);
  const value = useMemo(() => ({
    draft,
    setDraft,
    result,
    setResult,
    resetDraft: () => setDraft(defaultAnalysisDraft),
  }), [draft, result]);

  return (
    <AnalysisContext.Provider value={value}>
      {children}
    </AnalysisContext.Provider>
  );
}

export const useAnalysis = () => useContext(AnalysisContext);
