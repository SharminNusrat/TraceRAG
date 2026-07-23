import { useRef, useState } from 'react';
import { UploadCloud, FileText, X } from 'lucide-react';

const TYPE_OPTIONS = [
  { value: '', label: 'Select artifact type' },
  { value: 'requirements', label: 'Requirements' },
  { value: 'code', label: 'Code (.zip)' },
  // Add new artifact types here as the pipeline supports them
  // (e.g. architecture model) - no new dropzone needed.
];

function fileId(file) {
  return `${file.name}-${file.size}-${file.lastModified}`;
}

function inferType(file) {
  return file.name.toLowerCase().endsWith('.zip') ? 'code' : 'requirements';
}

export function ArtifactUploader({ files, onFilesChange, requirementsText, onRequirementsTextChange }) {
  const inputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [pasting, setPasting] = useState(Boolean(requirementsText));

  const addFiles = (fileList) => {
    const existingIds = new Set(files.map((f) => f.id));
    const incoming = Array.from(fileList)
      .map((file) => ({ id: fileId(file), file, type: inferType(file) }))
      .filter((f) => !existingIds.has(f.id));
    if (incoming.length) onFilesChange([...files, ...incoming]);
  };

  const removeFile = (id) => onFilesChange(files.filter((f) => f.id !== id));
  const setType = (id, type) => onFilesChange(files.map((f) => (f.id === id ? { ...f, type } : f)));

  const handleDrop = (event) => {
    event.preventDefault();
    setDragOver(false);
    addFiles(event.dataTransfer.files);
  };

  const pastedCount = requirementsText.trim() ? 1 : 0;
  const totalCount = files.length + pastedCount;
  const missingType = files.some((f) => !f.type);
  const hasEntries = files.length > 0 || pastedCount > 0;

  return (
    <div className="uploader">
      <label
        className={`dropzone${dragOver ? ' drag-over' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.zip"
          onChange={(event) => addFiles(event.target.files)}
        />
        <span><UploadCloud size={24} strokeWidth={1.7} /></span>
        <b>Drag and drop files here</b>
        <small>PDF, DOCX, or TXT for requirements &middot; ZIP for code &middot; up to 20&nbsp;MB each</small>
        <em>Select files</em>
      </label>

      <button type="button" className="text-toggle" onClick={() => setPasting((v) => !v)}>
        {pasting ? 'Hide text input' : 'Or paste requirements text instead'}
      </button>
      {pasting && (
        <textarea
          className="uploader-textarea"
          value={requirementsText}
          onChange={(event) => onRequirementsTextChange(event.target.value)}
          placeholder="Paste your requirements here..."
          rows="6"
        />
      )}

      {hasEntries && (
        <div className="file-list">
          <span className="file-list-label">
            Selected artifacts <b>{totalCount}</b>
          </span>

          {pastedCount > 0 && (
            <div className="file-row">
              <span className="file-row-icon"><FileText size={16} strokeWidth={2} /></span>
              <div className="file-row-name">
                <b>Pasted requirements text</b>
                <small>{requirementsText.trim().length.toLocaleString()} characters</small>
              </div>
              <span className="file-row-type-fixed">Requirements</span>
            </div>
          )}

          {files.map((entry) => (
            <div className="file-row" key={entry.id}>
              <span className="file-row-icon"><FileText size={16} strokeWidth={2} /></span>
              <div className="file-row-name">
                <b>{entry.file.name}</b>
                <small>{Math.ceil(entry.file.size / 1024).toLocaleString()} KB</small>
              </div>
              <select value={entry.type} onChange={(event) => setType(entry.id, event.target.value)}>
                {TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <button type="button" className="file-row-remove" onClick={() => removeFile(entry.id)} aria-label={`Remove ${entry.file.name}`}>
                <X size={15} strokeWidth={2.2} />
              </button>
            </div>
          ))}
        </div>
      )}

      {totalCount > 0 && totalCount < 2 && (
        <p className="form-hint warn">Add at least two artifacts &mdash; one requirements source and one codebase.</p>
      )}
      {missingType && (
        <p className="form-hint warn">Select an artifact type for each uploaded file.</p>
      )}
    </div>
  );
}
