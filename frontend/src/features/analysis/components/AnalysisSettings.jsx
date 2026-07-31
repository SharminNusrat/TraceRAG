import { ArrowRight, Layers } from 'lucide-react';
import { findKind, findPreprocessor, resolveSides } from '../api/capabilitiesApi';

/**
 * Two distinct concepts, deliberately labelled apart:
 *  - Preprocessor: how an artifact is split into comparable elements.
 *  - Output level: which level recovered links are reported at.
 */
function SideSettings({ title, artifact, summary, capabilities, preprocessorValue, outputLevelValue, onChange, prefix }) {
  const kind = findKind(capabilities, artifact?.kind);
  if (!kind) return null;

  const preprocessor = findPreprocessor(kind, preprocessorValue) ?? kind.preprocessors[0];
  const levels = preprocessor?.output_levels ?? [];

  return (
    <div className="side-settings">
      <div className="side-settings-header">
        <h4>{title}</h4>
        <span className="side-settings-artifact">{summary ?? artifact?.name ?? '—'}</span>
      </div>

      <label>
        Split into
        <select
          value={preprocessor?.key ?? ''}
          onChange={(event) => onChange(`${prefix}Preprocessor`, event.target.value)}
        >
          {kind.preprocessors.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
        <small className="field-hint">{preprocessor?.description}</small>
      </label>

      <label>
        Report links at
        <select
          value={outputLevelValue ?? ''}
          onChange={(event) => onChange(`${prefix}OutputLevel`, event.target.value || null)}
        >
          {levels.map((level) => (
            <option key={level.key} value={level.key}>{level.label}</option>
          ))}
        </select>
        <small className="field-hint">
          {levels.find((level) => level.key === outputLevelValue)?.description
            ?? 'Where each recovered link is anchored in the results.'}
        </small>
      </label>
    </div>
  );
}

const describeGroup = (artifacts, singular) => (
  artifacts.length === 1 ? artifacts[0].name : `${artifacts.length} ${singular}s combined`
);

function TraceScope({ sides, draft, onChange }) {
  const { sources, targets, selectedSources } = sides;

  // Nothing to decide: one artifact each side.
  if (sources.length <= 1 && targets.length <= 1) {
    return (
      <div className="trace-pair resolved">
        <span className="trace-pair-label">Tracing</span>
        <b>{sources[0]?.name ?? '—'}</b>
        <ArrowRight size={14} strokeWidth={2.4} />
        <b>{targets[0]?.name ?? '—'}</b>
      </div>
    );
  }

  return (
    <div className="trace-pair">
      <div className="trace-pair-header">
        <h4>What should be traced?</h4>
        <p>
          Uploads of the same kind are analysed together by default. You can narrow
          the source side to a single artifact.
        </p>
      </div>

      <div className="trace-scope-grid">
        <label>
          Source
          <select
            value={draft.sourceArtifactId ?? ''}
            onChange={(event) => onChange('sourceArtifactId', event.target.value || null)}
          >
            {sources.length > 1 && (
              <option value="">All sources together ({sources.length} artifacts)</option>
            )}
            {sources.map((artifact) => (
              <option key={artifact.id} value={artifact.id}>{artifact.name}</option>
            ))}
          </select>
          <small className="field-hint">
            {selectedSources.length > 1
              ? 'Every source artifact is analysed as one corpus.'
              : 'Only this artifact is traced.'}
          </small>
        </label>

        <span className="trace-pair-arrow"><ArrowRight size={16} strokeWidth={2.4} /></span>

        <div className="trace-target-summary">
          <span className="trace-target-label">Target</span>
          <div className="trace-target-value">
            <Layers size={14} strokeWidth={2} />
            <b>{describeGroup(targets, 'artifact')}</b>
          </div>
          <small className="field-hint">
            {targets.length > 1
              ? 'All target uploads are treated as one codebase. To trace against only part of it, upload just that folder, file or .zip.'
              : 'Treated as one codebase.'}
          </small>
        </div>
      </div>
    </div>
  );
}

export function AnalysisSettings({ draft, onChange, artifacts, capabilities }) {
  if (!capabilities) return <p className="form-hint">Loading analysis options…</p>;

  const sides = resolveSides(artifacts, capabilities, draft);

  return (
    <div className="analysis-settings">
      <TraceScope sides={sides} draft={draft} onChange={onChange} />

      <div className="side-settings-grid">
        <SideSettings
          title="Source"
          prefix="source"
          artifact={sides.selectedSources[0]}
          summary={describeGroup(sides.selectedSources, 'artifact')}
          capabilities={capabilities}
          preprocessorValue={draft.sourcePreprocessor}
          outputLevelValue={draft.sourceOutputLevel}
          onChange={onChange}
        />
        <SideSettings
          title="Target"
          prefix="target"
          artifact={sides.selectedTargets[0]}
          summary={describeGroup(sides.selectedTargets, 'artifact')}
          capabilities={capabilities}
          preprocessorValue={draft.targetPreprocessor}
          outputLevelValue={draft.targetOutputLevel}
          onChange={onChange}
        />
      </div>

      <div className="settings-grid">
        <label>
          Classifier
          <select
            value={draft.classifier}
            onChange={(event) => onChange('classifier', event.target.value)}
          >
            {capabilities.classifiers.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
          <small className="field-hint">
            {capabilities.classifiers.find((c) => c.key === draft.classifier)?.description}
          </small>
        </label>
        <label>
          Candidates per requirement
          <input
            min="1"
            max="25"
            type="number"
            value={draft.nResults}
            onChange={(event) => onChange('nResults', event.target.value)}
          />
          <small className="field-hint">How many code elements each requirement is compared against.</small>
        </label>
        <label>
          Dependency expansion depth
          <input
            min="0"
            max="3"
            type="number"
            value={draft.dependencyExpansionDepth}
            onChange={(event) => onChange('dependencyExpansionDepth', event.target.value)}
          />
          <small className="field-hint">
            Follow calls/inheritance this many hops from each matched element. 0 disables it.
          </small>
        </label>
      </div>
    </div>
  );
}
