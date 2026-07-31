import { createContext, useContext, useMemo, useState } from 'react';
import { defaultAnalysisDraft } from './analysisDefaults';

const AnalysisContext = createContext(null);

export function AnalysisProvider({ children }) {
  const [draft, setDraft] = useState(defaultAnalysisDraft);
  const [result, setResult] = useState(null);
  // How the result was produced - the results page needs the classifier to
  // explain *why* an explanation is missing.
  const [runMeta, setRunMeta] = useState(null);
  const value = useMemo(() => ({
    draft,
    setDraft,
    result,
    setResult,
    runMeta,
    setRunMeta,
    resetDraft: () => setDraft(defaultAnalysisDraft),
  }), [draft, result, runMeta]);

  return (
    <AnalysisContext.Provider value={value}>
      {children}
    </AnalysisContext.Provider>
  );
}

export const useAnalysis = () => useContext(AnalysisContext);
