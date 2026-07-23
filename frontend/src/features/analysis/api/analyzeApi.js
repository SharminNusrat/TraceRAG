import { httpClient } from '../../../services/httpClient';

/** Maps UI form fields to the current FastAPI AnalyzeRequest schema. */
export function toAnalyzeRequest(draft, projectId) {
  return {
    source_type: draft.sourceType,
    requirements_path: '',
    requirements_text: draft.requirementsText,
    codebase_path: '',
    source_preprocessor: draft.sourcePreprocessor,
    target_preprocessor: draft.targetPreprocessor,
    classifier: draft.classifier,
    n_results: Number(draft.nResults),
    source_granularity: Number(draft.sourceGranularity),
    target_granularity: Number(draft.targetGranularity),
    dependency_expansion_depth: Number(draft.dependencyExpansionDepth),
    analysis_mode: projectId ? 'project' : 'session',
    project_id: projectId ?? null,
  };
}
/** Ready to enable when the backend can accept browser-uploaded artifacts. */
export const runAnalysis = (draft, projectId) => httpClient('/analyze', {
  method: 'POST',
  body: JSON.stringify(toAnalyzeRequest(draft, projectId)),
});
