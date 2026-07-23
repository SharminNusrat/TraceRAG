import { FileText, Settings, ArrowRight } from 'lucide-react';

export function ReviewStep({ files, requirementsText, draft, onGoToStep, onRun, canRun }) {
  const pastedCount = requirementsText.trim() ? 1 : 0;
  const totalArtifacts = files.length + pastedCount;

  const settingsDisplay = [
    ['Requirements granularity', draft.sourcePreprocessor],
    ['Code granularity', draft.targetPreprocessor],
    ['Classifier', draft.classifier],
    ['Links per requirement', draft.nResults],
  ];

  return (
    <div className="review-step">
      <div className="review-section">
        <div className="review-section-header">
          <div className="review-section-icon">
            <FileText size={18} strokeWidth={1.8} />
          </div>
          <div>
            <h3>Uploaded Artifacts</h3>
            <p>{totalArtifacts} artifact{totalArtifacts !== 1 ? 's' : ''} selected</p>
          </div>
          <button type="button" className="review-edit-link" onClick={() => onGoToStep(0)}>
            Edit
          </button>
        </div>

        <div className="review-list">
          {pastedCount > 0 && (
            <div className="review-list-item">
              <span className="review-item-badge requirements">REQ</span>
              <div className="review-item-info">
                <b>Pasted requirements text</b>
                <small>{requirementsText.trim().length.toLocaleString()} characters</small>
              </div>
            </div>
          )}
          {files.map((entry) => (
            <div className="review-list-item" key={entry.id}>
              <span className={`review-item-badge ${entry.type}`}>
                {entry.type === 'code' ? 'CODE' : 'REQ'}
              </span>
              <div className="review-item-info">
                <b>{entry.file.name}</b>
                <small>{Math.ceil(entry.file.size / 1024).toLocaleString()} KB</small>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="review-section">
        <div className="review-section-header">
          <div className="review-section-icon">
            <Settings size={18} strokeWidth={1.8} />
          </div>
          <div>
            <h3>Analysis Settings</h3>
            <p>Configuration for this run</p>
          </div>
          <button type="button" className="review-edit-link" onClick={() => onGoToStep(1)}>
            Edit
          </button>
        </div>

        <div className="review-settings-grid">
          {settingsDisplay.map(([label, value]) => (
            <div className="review-setting" key={label}>
              <span className="review-setting-label">{label}</span>
              <span className="review-setting-value">{value}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="review-run-area">
        <p>Your files and results stay in this browser until you decide to save them.</p>
        <button
          type="button"
          className="button button-primary review-run-button"
          onClick={onRun}
          disabled={!canRun}
        >
          Run analysis <ArrowRight size={16} strokeWidth={2.2} />
        </button>
      </div>
    </div>
  );
}
