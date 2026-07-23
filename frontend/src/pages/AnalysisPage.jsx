import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Brand } from '../components/common/Brand';
import { Button } from '../components/common/Button';
import { PageHeader } from '../components/common/PageHeader';
import { useAnalysis } from '../features/analysis/AnalysisContext';
import { AnalysisSettings } from '../features/analysis/components/AnalysisSettings';
import { ArtifactUploader } from '../features/analysis/components/ArtifactUploader';
import { mockResult } from '../features/analysis/mockResult';

export function AnalysisPage() {
  const navigate = useNavigate();
  const { draft, setDraft, setResult } = useAnalysis();
  const [files, setFiles] = useState([]);
  const [advanced, setAdvanced] = useState(false);

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
  const canRun = totalArtifacts >= 2 && !missingType;

  const run = () => {
    setResult(mockResult);
    navigate('/results');
  };

  return (
    <main className="workflow-page">
      <nav className="workflow-nav">
        <Brand />
        <span>New analysis</span>
        <Button variant="ghost" onClick={() => navigate('/')}>Exit</Button>
      </nav>

      <div className="workflow-content">
        <PageHeader
          eyebrow="One-time analysis"
          title="Set up your analysis"
          description="Add your requirements and codebase. You can review results before choosing whether to save them."
        />

        <section className="setup-grid">
          <article className="setup-card">
            <div className="step-label">01</div>
            <h2>Add your artifacts</h2>
            <p>Upload the files for this analysis and mark what each one is. At least two are needed: a requirements source and a codebase.</p>
            <ArtifactUploader
              files={files}
              onFilesChange={handleFilesChange}
              requirementsText={draft.requirementsText}
              onRequirementsTextChange={handleRequirementsText}
            />
          </article>

          <article className="setup-card settings-card">
            <div className="step-label">02</div>
            <h2>Analysis settings</h2>
            <p>Smart defaults work well for most repositories.</p>
            <button className="settings-summary" onClick={() => setAdvanced(!advanced)}>
              <span>
                <b>{advanced ? 'Hide settings' : 'Configure analysis'}</b>
                <small>Section requirements &middot; Method-level code &middot; Reasoning classifier</small>
              </span>
              <i>{advanced ? '⌃' : '⌄'}</i>
            </button>
            {advanced && <AnalysisSettings draft={draft} onChange={update} />}
          </article>
        </section>

        <footer className="workflow-footer">
          <p>Your files and results stay in this browser until you decide to save them.</p>
          <Button onClick={run} disabled={!canRun}>Run analysis <span>→</span></Button>
        </footer>
      </div>
    </main>
  );
}
