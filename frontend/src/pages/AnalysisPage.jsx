import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, X } from 'lucide-react';
import { Brand } from '../components/common/Brand';
import { Stepper } from '../components/common/Stepper';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { AnalysisSettings } from '../features/analysis/components/AnalysisSettings';
import { ArtifactUploader } from '../features/analysis/components/ArtifactUploader';
import { ReviewStep } from '../features/analysis/components/ReviewStep';
import { mockResult } from '../features/analysis/mockResult';

const STEPS = ['Upload Artifacts', 'Analysis Settings', 'Review & Run'];

export function AnalysisPage() {
  const navigate = useNavigate();
  const { draft, setDraft, setResult } = useAnalysis();
  const [files, setFiles] = useState([]);
  const [currentStep, setCurrentStep] = useState(0);

  const update = (field, value) => setDraft({ ...draft, [field]: value });

  const handleFilesChange = (nextFiles) => {
    setFiles(nextFiles);
    const codeFile = nextFiles.find((f) => f.type === 'code');
    const reqFile = nextFiles.find((f) => f.type === 'requirements');
    setDraft({
      ...draft,
      codebaseFile: codeFile?.file ?? null,
      requirementsFile: reqFile?.file ?? null,
      sourceType: draft.requirementsText ? 'text' : 'document',
    });
  };

  const handleRequirementsText = (text) => {
    setDraft({ ...draft, requirementsText: text, sourceType: text ? 'text' : 'document' });
  };

  const totalArtifacts = files.length + (draft.requirementsText.trim() ? 1 : 0);
  const missingType = files.some((f) => !f.type);
  const canProceedFromUpload = totalArtifacts >= 2 && !missingType;
  const canRun = canProceedFromUpload;

  const next = () => {
    if (currentStep < STEPS.length - 1) setCurrentStep(currentStep + 1);
  };
  const back = () => {
    if (currentStep > 0) setCurrentStep(currentStep - 1);
  };
  const goToStep = (step) => {
    if (step <= currentStep) setCurrentStep(step);
  };

  const run = () => {
    setResult(mockResult);
    navigate('/results');
  };

  const canNext = currentStep === 0 ? canProceedFromUpload : true;

  return (
    <main className="workflow-page">
      <nav className="workflow-nav">
        <Brand />
        <span>New analysis</span>
        <button
          type="button"
          className="workflow-exit-btn"
          onClick={() => navigate('/')}
          aria-label="Exit analysis"
        >
          <X size={15} strokeWidth={2.2} />
          <span>Exit</span>
        </button>
      </nav>

      <div className="workflow-content">
        <Stepper steps={STEPS} currentStep={currentStep} onStepClick={goToStep} />

        <div className="step-content-card">
          {currentStep === 0 && (
            <>
              <div className="step-content-header">
                <h1>Upload your artifacts</h1>
                <p>
                  Add the requirements and codebase files for analysis. You need at least
                  one requirements source and one codebase archive.
                </p>
              </div>
              <ArtifactUploader
                files={files}
                onFilesChange={handleFilesChange}
                requirementsText={draft.requirementsText}
                onRequirementsTextChange={handleRequirementsText}
              />
            </>
          )}

          {currentStep === 1 && (
            <>
              <div className="step-content-header">
                <h1>Analysis settings</h1>
                <p>
                  Configure how your artifacts are processed. The defaults work
                  well for most repositories.
                </p>
              </div>
              <AnalysisSettings draft={draft} onChange={update} />
            </>
          )}

          {currentStep === 2 && (
            <ReviewStep
              files={files}
              requirementsText={draft.requirementsText}
              draft={draft}
              onGoToStep={goToStep}
              onRun={run}
              canRun={canRun}
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
