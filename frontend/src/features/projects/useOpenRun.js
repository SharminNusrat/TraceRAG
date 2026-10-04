import { useNavigate } from 'react-router-dom';
import { useAnalysis } from '../analysis/AnalysisContext';
import { getAnalysis, kindOfSide, runTimestamp } from './api/projectsApi';

/**
 * Opening a saved run in the results view.
 *
 * A saved run is stored in the same shape a live run returns, so it is pushed
 * straight into the analysis context and rendered by the existing results
 * view rather than a second read-only one.
 */
export function useOpenRun() {
  const navigate = useNavigate();
  const { setResult, setRunMeta } = useAnalysis();

  /** Show a run that has already been fetched. */
  const show = (detail) => {
    setResult(detail.result);
    setRunMeta({
      classifier: detail.config.classifier,
      duration: detail.execution_duration,
      // The whole configuration, not just the classifier: the preprocessors
      // and output levels decide what the identifiers in this result refer to,
      // so a reopened run is not readable without them.
      config: detail.config,
      sourceKind: kindOfSide(detail.artifacts, 'source'),
      targetKind: kindOfSide(detail.artifacts, 'target'),
      // Lets the results view show - and hand back - the files this run was
      // actually performed against.
      artifacts: detail.artifacts,
      savedAs: runTimestamp(detail),
      // It is already stored. Without this the results view believes it is
      // looking at an unsaved run and offers to save it, which files a copy.
      savedTo: {
        analysis_id: detail.analysis_id,
        project_id: detail.project_id,
        project_name: detail.project_name,
      },
    });
    navigate('/results');
  };

  /** Fetch a run by id and show it. */
  const open = async (analysisId) => show(await getAnalysis(analysisId));

  return { open, show };
}
