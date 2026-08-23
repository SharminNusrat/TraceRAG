import { ArrowRight } from 'lucide-react';
import { findKind, findPreprocessor, resolveSides } from '../api/capabilitiesApi';

// Dependency expansion walks a call graph, so the backend runs it only when
// the target side is source code and ignores the setting otherwise. Mirrored
// here so the control says so instead of quietly doing nothing.
const KIND_CODE = 'code';

const ANALYSIS_MODES = [
  ['project', 'Reuse embeddings (project)',
    'Keeps the embedding cache between runs, so re-running the same artifacts skips work already done.'],
  ['session', 'Fresh each run (session)',
    'Embeds everything from scratch and keeps nothing. Use when comparing runs that must not share state.'],
];

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

/** What this run will trace between. Which side each artifact is on is set in
 *  the upload step, so this only reports the resulting pair. */
function TracePair({ sides }) {
  const { selectedSources, selectedTargets } = sides;
  return (
    <div className="trace-pair resolved">
      <span className="trace-pair-label">Tracing</span>
      <b>{selectedSources.length ? describeGroup(selectedSources, 'artifact') : '—'}</b>
      <ArrowRight size={14} strokeWidth={2.4} />
      <b>{selectedTargets.length ? describeGroup(selectedTargets, 'artifact') : '—'}</b>
    </div>
  );
}

export function AnalysisSettings({ draft, onChange, artifacts, capabilities }) {
  if (!capabilities) return <p className="form-hint">Loading analysis options…</p>;

  const sides = resolveSides(artifacts, capabilities);

  // Retrieval runs per source element, so the control is named after whatever
  // the source side is currently being split into.
  const sourceKind = findKind(capabilities, sides.selectedSources[0]?.kind);
  const sourceLevel = findPreprocessor(sourceKind, draft.sourcePreprocessor)
    ?.output_levels.find((level) => level.key === draft.sourceOutputLevel);
  const sourceNoun = sourceLevel ? sourceLevel.label.toLowerCase() : 'source element';
  const targetIsCode = sides.selectedTargets[0]?.kind === KIND_CODE;

  // Only kinds that are not prose gain anything from a summary, and the
  // registry says which those are.
  const targetKind = findKind(capabilities, sides.selectedTargets[0]?.kind);
  const summarisable = [sourceKind, targetKind]
    .filter((kind) => kind?.summarize)
    .map((kind) => kind.label.toLowerCase());

  return (
    <div className="analysis-settings">
      <TracePair sides={sides} />

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
          Candidates per {sourceNoun}
          <input
            min="1"
            max="25"
            type="number"
            value={draft.nResults}
            onChange={(event) => onChange('nResults', event.target.value)}
          />
          <small className="field-hint">
            How many target elements each {sourceNoun} is compared against.
          </small>
        </label>
        <label>
          Dependency expansion depth
          <input
            min="0"
            max="3"
            type="number"
            value={draft.dependencyExpansionDepth}
            onChange={(event) => onChange('dependencyExpansionDepth', event.target.value)}
            disabled={!targetIsCode}
          />
          <small className="field-hint">
            {targetIsCode
              ? 'Follow calls/inheritance this many hops from each matched element. 0 disables it.'
              : 'Only applies when the target side is source code — expansion walks a call graph.'}
          </small>
        </label>
        <label>
          Embedding reuse
          <select
            value={draft.analysisMode}
            onChange={(event) => onChange('analysisMode', event.target.value)}
          >
            {ANALYSIS_MODES.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <small className="field-hint">
            {ANALYSIS_MODES.find(([value]) => value === draft.analysisMode)?.[2]}
          </small>
        </label>
        <label>
          Summarize before embedding
          <select
            value={draft.summarizeElements ? 'on' : 'off'}
            onChange={(event) => onChange('summarizeElements', event.target.value === 'on')}
            disabled={!summarisable.length}
          >
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
          <small className="field-hint">
            {summarisable.length
              ? `Describes each ${summarisable.join(' and ')} element in one sentence `
                + 'so it embeds on meaning, not just identifiers. Costs extra model calls '
                + 'the first time; reused afterwards.'
              : 'Both sides are already written in prose, so there is nothing to summarize.'}
          </small>
        </label>
      </div>
    </div>
  );
}
