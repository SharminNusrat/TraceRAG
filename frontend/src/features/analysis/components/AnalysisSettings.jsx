const options = {
  sourcePreprocessor: [['section', 'Sections'], ['sentence', 'Sentences'], ['single', 'Whole document']],
  targetPreprocessor: [['method', 'Methods'], ['line', 'Code chunks'], ['tree', 'Syntax tree']],
  classifier: [['reasoning', 'Reasoning classifier'], ['simple', 'Simple classifier']],
};

function fieldLabel(field) {
  if (field === 'sourcePreprocessor') return 'Requirements granularity';
  if (field === 'targetPreprocessor') return 'Code granularity';
  return 'Classifier';
}

export function AnalysisSettings({ draft, onChange }) {
  return (
    <div className="settings-grid">
      {Object.entries(options).map(([field, values]) => (
        <label key={field}>
          {fieldLabel(field)}
          <select
            value={draft[field]}
            onChange={(event) => onChange(field, event.target.value)}
          >
            {values.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
      ))}
      <label>
        Links per requirement
        <input
          min="1"
          max="25"
          type="number"
          value={draft.nResults}
          onChange={(event) => onChange('nResults', event.target.value)}
        />
      </label>
    </div>
  );
}
