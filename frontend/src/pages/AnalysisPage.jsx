import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { WorkflowNav } from '../components/common/WorkflowNav';
import { Stepper } from '../components/common/Stepper';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { AnalysisSettings } from '../features/analysis/components/AnalysisSettings';
import { ArtifactUploader } from '../features/analysis/components/ArtifactUploader';
import { ReviewStep } from '../features/analysis/components/ReviewStep';
import { runAnalysisUpload } from '../features/analysis/api/analyzeApi';
import { getProject, saveAnalysis } from '../features/projects/api/projectsApi';
import {
  findKind,
  findPreprocessor,
  resolveSides,
  useCapabilities,
} from '../features/analysis/api/capabilitiesApi';

const STEPS = ['Upload Artifacts', 'Analysis Settings', 'Review & Run'];

export function AnalysisPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { draft, setDraft, setResult, setRunMeta } = useAnalysis();
  const { capabilities, error: capabilitiesError } = useCapabilities();
  const [artifacts, setArtifacts] = useState([]);
  const [currentStep, setCurrentStep] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  // Started from a project, so the run already knows where it belongs and is
  // filed there on completion instead of asking again afterwards.
  const projectId = params.get('project');
  const [project, setProject] = useState(null);

  useEffect(() => {
    if (!projectId) return;
    getProject(projectId).then(setProject).catch(() => setProject(null));
  }, [projectId]);

  const update = (field, value) => setDraft((current) => ({ ...current, [field]: value }));

  const sides = resolveSides(artifacts, capabilities, draft);

  // Adopt the backend's run defaults once, so values set in AnalyzeRequest
  // (classifier, n_results, dependency depth, analysis mode) actually apply
  // instead of being overwritten by the frontend's placeholders.
  const [defaultsApplied, setDefaultsApplied] = useState(false);
  useEffect(() => {
    if (!capabilities?.defaults || defaultsApplied) return;
    const { classifier, n_results, dependency_expansion_depth, analysis_mode } = capabilities.defaults;
    setDraft((current) => ({
      ...current,
      classifier,
      nResults: n_results,
      dependencyExpansionDepth: dependency_expansion_depth,
      analysisMode: analysis_mode,
    }));
    setDefaultsApplied(true);
  }, [capabilities, defaultsApplied]);

  // Drop a pinned source that no longer exists, and keep each side's
  // preprocessor/output level valid for the kind it is pointing at.
  useEffect(() => {
    if (!capabilities) return;

    setDraft((current) => {
      const next = { ...current };

      const pinnedStillExists = sides.sources.some((a) => a.id === current.sourceArtifactId);
      if (current.sourceArtifactId && !pinnedStillExists) next.sourceArtifactId = null;

      for (const [artifact, prefix] of [
        [sides.selectedSources[0], 'source'],
        [sides.selectedTargets[0], 'target'],
      ]) {
        const kind = findKind(capabilities, artifact?.kind);
        if (!kind) continue;

        const preprocessorField = `${prefix}Preprocessor`;
        const levelField = `${prefix}OutputLevel`;
        let preprocessor = findPreprocessor(kind, next[preprocessorField]);
        if (!preprocessor) {
          preprocessor = findPreprocessor(kind, kind.default_preprocessor) ?? kind.preprocessors[0];
          next[preprocessorField] = preprocessor?.key ?? null;
        }

        const allowed = preprocessor?.output_levels ?? [];
        if (!allowed.some((level) => level.key === next[levelField])) {
          const preferred = allowed.find((level) => level.key === kind.default_output_level);
          next[levelField] = (preferred ?? allowed[0])?.key ?? null;
        }
      }

      const unchanged = Object.keys(next).every((key) => next[key] === current[key]);
      return unchanged ? current : next;
    });
  }, [capabilities, artifacts, draft.sourceArtifactId, draft.sourcePreprocessor, draft.targetPreprocessor]);

  const untyped = artifacts.some((artifact) => !artifact.kind);
  const withinBudget = !capabilities || artifacts.reduce(
    (sum, artifact) => sum + artifact.entries.reduce((n, entry) => n + entry.file.size, 0),
    0,
  ) <= capabilities.max_total_upload_bytes;

  const canProceedFromUpload =
    sides.sources.length > 0 && sides.targets.length > 0 && !untyped && withinBudget;
  const canRun = canProceedFromUpload
    && sides.sourceIds.length > 0 && sides.targetIds.length > 0;

  const next = () => { if (currentStep < STEPS.length - 1) setCurrentStep(currentStep + 1); };
  const back = () => { if (currentStep > 0) setCurrentStep(currentStep - 1); };
  const goToStep = (step) => { if (step <= currentStep) setCurrentStep(step); };

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const startedAt = performance.now();
      const response = await runAnalysisUpload(draft, artifacts, sides);
      const duration = (performance.now() - startedAt) / 1000;
      setResult(response);

      const meta = { classifier: draft.classifier, duration };

      if (projectId) {
        try {
          const saved = await saveAnalysis(projectId, { draft, result: response, duration });
          meta.savedTo = saved;
        } catch (saveError) {
          // The analysis itself succeeded, so show it either way and let the
          // results page offer a manual save rather than losing the run.
          meta.saveError = saveError.message;
        }
      }

      setRunMeta(meta);
      navigate('/results');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setRunning(false);
    }
  };

  const canNext = currentStep === 0 ? canProceedFromUpload : true;

  return (
    <main className="workflow-page">
      <WorkflowNav title="New Analysis" exitLabel="Exit Analysis" />

      <div className="workflow-content">
        {project && (
          <p className="workflow-target">
            Results will be saved to <b>{project.project_name}</b>.
          </p>
        )}

        <Stepper steps={STEPS} currentStep={currentStep} onStepClick={goToStep} />

        <div className="step-content-card">
          {capabilitiesError && (
            <p className="form-hint warn">
              Could not reach the analysis service: {capabilitiesError}
            </p>
          )}

          {currentStep === 0 && (
            <>
              <div className="step-content-header">
                <h1>Upload Your Artifacts</h1>
                <p>
                  Add the requirements and code you want to trace between. Code can be
                  individual files, a whole folder, or a .zip archive.
                </p>
              </div>
              <ArtifactUploader
                artifacts={artifacts}
                onArtifactsChange={setArtifacts}
                capabilities={capabilities}
              />
            </>
          )}

          {currentStep === 1 && (
            <>
              <div className="step-content-header">
                <h1>Analysis Settings</h1>
                <p>
                  Choose how each artifact is split, and the level you want recovered
                  links reported at. The defaults work well for most repositories.
                </p>
              </div>
              <AnalysisSettings
                draft={draft}
                onChange={update}
                artifacts={artifacts}
                capabilities={capabilities}
              />
            </>
          )}

          {currentStep === 2 && (
            <ReviewStep
              artifacts={artifacts}
              sides={sides}
              draft={draft}
              capabilities={capabilities}
              onGoToStep={goToStep}
              onRun={run}
              canRun={canRun}
              running={running}
              error={error}
            />
          )}
        </div>

        {/* Step navigation footer */}
        <footer className="step-footer">
          <div className="step-footer-left">
            {currentStep > 0 && (
              <button type="button" className="button button-secondary step-nav-btn" onClick={back}>
                <ArrowLeft size={15} strokeWidth={2.2} /> Back
              </button>
            )}
          </div>
          <div className="step-footer-right">
            {currentStep < STEPS.length - 1 && (
              <button
                type="button"
                className="button button-primary step-nav-btn"
                onClick={next}
                disabled={!canNext}
              >
                Next <ArrowRight size={15} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </footer>
      </div>
    </main>
  );
}
