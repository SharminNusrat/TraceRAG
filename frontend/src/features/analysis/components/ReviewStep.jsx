import { FileText, Settings, ArrowRight, Loader2 } from 'lucide-react';
import { findKind, findPreprocessor } from '../api/capabilitiesApi';

function describeSide(capabilities, artifact, preprocessorKey, outputLevelKey) {
  const kind = findKind(capabilities, artifact?.kind);
  const preprocessor = findPreprocessor(kind, preprocessorKey);
  const level = preprocessor?.output_levels.find((option) => option.key === outputLevelKey);
  return {
    preprocessor: preprocessor?.label ?? preprocessorKey ?? '—',
    level: level?.label ?? '—',
  };
}

/**
 * Indeterminate on purpose: the run is a single blocking request, so there is
 * no real percentage to report. A bar that pretends otherwise would be a lie.
 */
function RunProgress() {
  return (
    <div className="run-progress" role="progressbar" aria-label="Analysis in progress">
      <span />
    </div>
  );
}

export function ReviewStep({ artifacts, sides, draft, capabilities, onGoToStep, onRun, canRun, running, error }) {
  const sourceArtifact = sides.selectedSources[0];
  const targetArtifact = sides.selectedTargets[0];

  const source = describeSide(capabilities, sourceArtifact, draft.sourcePreprocessor, draft.sourceOutputLevel);
  const target = describeSide(capabilities, targetArtifact, draft.targetPreprocessor, draft.targetOutputLevel);

  const classifierLabel = capabilities?.classifiers
    .find((option) => option.key === draft.classifier)?.label ?? draft.classifier;

  const settingsDisplay = [
    ['Source split into', source.preprocessor],
    ['Source links reported at', source.level],
    ['Target split into', target.preprocessor],
    ['Target links reported at', target.level],
    ['Classifier', classifierLabel],
    ['Candidates per source element', draft.nResults],
    ['Dependency expansion', Number(draft.dependencyExpansionDepth)
      ? `${draft.dependencyExpansionDepth} hop${Number(draft.dependencyExpansionDepth) === 1 ? '' : 's'}`
      : 'Off'],
    ['Embedding reuse', draft.analysisMode === 'project' ? 'Reused between runs' : 'Fresh each run'],
    ['Summarize before embedding', draft.summarizeElements ? 'On' : 'Off'],
  ];

  return (
    <div className="review-step">
      <div className="review-section">
        <div className="review-section-header">
          <div className="review-section-icon">
            <FileText size={18} strokeWidth={1.8} />
          </div>
          <div>
            <h3>Trace pair</h3>
            <p>{artifacts.length} artifact{artifacts.length !== 1 ? 's' : ''} uploaded</p>
          </div>
          <button type="button" className="review-edit-link" onClick={() => onGoToStep(0)}>
            Edit
          </button>
        </div>

        <div className="review-list">
          {[['Source', sides.selectedSources], ['Target', sides.selectedTargets]].map(([role, group]) => {
            const fileCount = group.reduce((sum, a) => sum + a.entries.length, 0);
            const pasted = group.filter((a) => a.text !== undefined).length;
            return (
              <div className="review-list-item" key={role}>
                <span className={`review-item-badge ${role.toLowerCase()}`}>{role}</span>
                <div className="review-item-info">
                  <b>
                    {group.length === 0 ? 'Not selected'
                      : group.length === 1 ? group[0].name
                        : `${group.length} artifacts combined`}
                  </b>
                  <small>
                    {group.length > 1 && `${group.map((a) => a.name).join(', ')} · `}
                    {fileCount} file{fileCount === 1 ? '' : 's'}
                    {pasted > 0 && ` · ${pasted} pasted`}
                  </small>
                </div>
              </div>
            );
          })}
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
        <p>
          {running
            ? 'Embedding artifacts and classifying trace links. This can take a few minutes.'
            : 'Your files and results stay in this browser until you decide to save them.'}
        </p>
        <button
          type="button"
          className="button button-primary review-run-button"
          onClick={onRun}
          disabled={!canRun || running}
        >
          {running ? (
            <><Loader2 size={17} strokeWidth={2.4} className="spinner" /> Running analysis…</>
          ) : (
            <>Run analysis <ArrowRight size={16} strokeWidth={2.2} /></>
          )}
        </button>
        {running && <RunProgress />}
        {error && <p className="form-hint warn">{error}</p>}
      </div>
    </div>
  );
}
