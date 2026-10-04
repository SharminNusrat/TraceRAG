import { useRef, useState } from 'react';
import {
  UploadCloud, FileText, Folder, FileArchive, X, Plus, Type,
} from 'lucide-react';
import {
  SIDE_SOURCE,
  SIDE_TARGET,
  SIDE_UNUSED,
  canTakeSide,
  defaultSide,
  findKind,
  kindForPath,
  resolveSides,
} from '../api/capabilitiesApi';

const SIDE_OPTIONS = [
  [SIDE_SOURCE, 'Trace from'],
  [SIDE_TARGET, 'Trace to'],
  [SIDE_UNUSED, 'Not used'],
];

let artifactCounter = 0;
const nextArtifactId = () => `artifact-${++artifactCounter}`;

/** Browsers expose a folder pick as files carrying webkitRelativePath. */
const entryPath = (file) => file.webkitRelativePath || file.name;

function artifactName(entries, isFolder, kindLabel) {
  if (isFolder) {
    const [first] = entries[0].path.split('/');
    return first || 'folder';
  }
  if (entries.length === 1) return entries[0].path.split('/').at(-1);
  // Deliberately not a count. This name outlives the upload it was made from -
  // it becomes the project's name for that source - so "7 files" would go on
  // saying seven long after an eighth was uploaded. How many there are now is
  // shown beside it, where it is read fresh each time.
  return kindLabel ?? 'Uploaded files';
}

/** Keep a name distinct from the ones already in the list. */
function uniqueName(name, artifacts) {
  const taken = new Set(artifacts.map((artifact) => artifact.name));
  if (!taken.has(name)) return name;
  let suffix = 2;
  while (taken.has(`${name} ${suffix}`)) suffix += 1;
  return `${name} ${suffix}`;
}

const totalBytes = (artifact) =>
  artifact.entries.reduce((sum, entry) => sum + entry.file.size, 0);

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.ceil(bytes / 1024)).toLocaleString()} KB`;
}

function ArtifactIcon({ artifact }) {
  if (artifact.text !== undefined) return <Type size={16} strokeWidth={2} />;
  if (artifact.isFolder) return <Folder size={16} strokeWidth={2} />;
  if (artifact.entries.some((entry) => entry.path.toLowerCase().endsWith('.zip'))) {
    return <FileArchive size={16} strokeWidth={2} />;
  }
  return <FileText size={16} strokeWidth={2} />;
}

export function ArtifactUploader({ artifacts, onArtifactsChange, capabilities }) {
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [skippedCount, setSkippedCount] = useState(0);

  const acceptAttribute = capabilities
    ? [...new Set(capabilities.artifact_kinds.flatMap((kind) => [
      ...kind.extensions,
      ...(kind.accepts_archive ? ['.zip'] : []),
    ]))].join(',')
    : undefined;

  // One add-action produces one artifact *per kind*: picking ten .js files
  // together means one codebase, but picking a requirements PDF alongside a
  // code .zip must not fuse them into a single artifact.
  const addArtifact = (fileList, isFolder) => {
    const entries = Array.from(fileList).map((file) => ({ file, path: entryPath(file) }));
    if (!entries.length) return;

    const byKind = new Map();
    let skipped = 0;
    for (const entry of entries) {
      const kind = kindForPath(capabilities, entry.path);
      if (!kind) { skipped += 1; continue; }
      if (!byKind.has(kind)) byKind.set(kind, []);
      byKind.get(kind).push(entry);
    }

    setSkippedCount(skipped);
    if (!byKind.size) return;

    const labelFor = (kind) =>
      capabilities?.artifact_kinds.find((option) => option.key === kind)?.label;

    const split = byKind.size > 1;
    // Each new artifact is placed against the ones already there, so a second
    // kind lands opposite the first instead of piling onto the same side.
    const merged = [...artifacts];
    for (const [kind, kindEntries] of byKind) {
      const label = labelFor(kind);
      const base = artifactName(kindEntries, isFolder, label);
      const artifact = {
        id: nextArtifactId(),
        // When one selection covers several kinds the shared folder name would
        // be ambiguous, so qualify each piece with its kind - unless the name
        // is already that kind, which needs no qualifying.
        name: uniqueName(
          split && label && base !== label ? `${base} (${label})` : base,
          merged,
        ),
        kind,
        entries: kindEntries,
        isFolder,
      };
      merged.push({ ...artifact, side: defaultSide(merged, artifact, capabilities) });
    }

    onArtifactsChange(merged);
  };

  const addTextArtifact = (text) => {
    const textKind = capabilities?.artifact_kinds.find((kind) => kind.accepts_text);
    if (!textKind) return;
    const artifact = {
      id: nextArtifactId(),
      name: `Pasted ${textKind.label.toLowerCase()}`,
      kind: textKind.key,
      entries: [],
      text,
    };
    onArtifactsChange([
      ...artifacts,
      { ...artifact, side: defaultSide(artifacts, artifact, capabilities) },
    ]);
  };

  const removeArtifact = (id) =>
    onArtifactsChange(artifacts.filter((artifact) => artifact.id !== id));

  const update = (id, changes) =>
    onArtifactsChange(artifacts.map((a) => (a.id === id ? { ...a, ...changes } : a)));

  const handleDrop = (event) => {
    event.preventDefault();
    setDragOver(false);
    addArtifact(event.dataTransfer.files, false);
  };

  return (
    <div className="uploader">
      <div
        className={`dropzone${dragOver ? ' drag-over' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          accept={acceptAttribute}
          onChange={(event) => { addArtifact(event.target.files, false); event.target.value = ''; }}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          hidden
          webkitdirectory=""
          directory=""
          onChange={(event) => { addArtifact(event.target.files, true); event.target.value = ''; }}
        />

        <span><UploadCloud size={24} strokeWidth={1.7} /></span>
        <b>Drag and Drop Files Here</b>
        {/* Listed from /capabilities rather than spelled out, so a newly
            supported artifact kind announces itself here with no edit. */}
        <small>
          {capabilities?.artifact_kinds
            .map((kind) => `${kind.extensions.join(', ')}${kind.accepts_archive ? ', .zip' : ''} for ${kind.label.toLowerCase()}`)
            .join(' · ')}
          {capabilities && ` · up to ${Math.round(capabilities.max_total_upload_bytes / 1024 / 1024)} MB total`}
        </small>
        <div className="dropzone-actions">
          <button type="button" className="button button-secondary" onClick={() => fileInputRef.current?.click()}>
            <Plus size={14} strokeWidth={2.4} /> Select files
          </button>
          <button type="button" className="button button-secondary" onClick={() => folderInputRef.current?.click()}>
            <Folder size={14} strokeWidth={2.2} /> Select folder
          </button>
        </div>
      </div>

      <button type="button" className="text-toggle" onClick={() => setPasting((value) => !value)}>
        {pasting ? 'Hide text input' : 'Or paste requirements text instead'}
      </button>
      {pasting && (
        <PasteBox onAdd={(text) => { addTextArtifact(text); setPasting(false); }} />
      )}

      {artifacts.length > 0 && (
        <div className="file-list">
          <span className="file-list-label">
            Artifacts to analyse <b>{artifacts.length}</b>
          </span>

          {artifacts.map((artifact) => (
            <div className="file-row" key={artifact.id}>
              <span className="file-row-icon"><ArtifactIcon artifact={artifact} /></span>
              <div className="file-row-name">
                <b>{artifact.name}</b>
                <small>
                  {artifact.text !== undefined
                    ? `${artifact.text.trim().length.toLocaleString()} characters`
                    : `${artifact.entries.length} file${artifact.entries.length === 1 ? '' : 's'} · ${formatBytes(totalBytes(artifact))}`}
                </small>
              </div>
              <select
                value={artifact.kind ?? ''}
                onChange={(event) => update(artifact.id, { kind: event.target.value })}
                aria-label={`Artifact type for ${artifact.name}`}
              >
                <option value="">Select type</option>
                {capabilities?.artifact_kinds.map((kind) => (
                  <option
                    key={kind.key}
                    value={kind.key}
                    disabled={artifact.text !== undefined && !kind.accepts_text}
                  >
                    {kind.label}
                  </option>
                ))}
              </select>
              {/* Any kind can be either side, so which side this artifact is on
                  is a choice, not something the file type decides. */}
              <select
                className={`side-select ${artifact.side ?? SIDE_UNUSED}`}
                value={artifact.side ?? SIDE_UNUSED}
                onChange={(event) => update(artifact.id, { side: event.target.value })}
                aria-label={`Trace side for ${artifact.name}`}
              >
                {SIDE_OPTIONS.map(([value, label]) => (
                  <option
                    key={value}
                    value={value}
                    disabled={value !== SIDE_UNUSED && !canTakeSide(capabilities, artifact, value)}
                  >
                    {label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="file-row-remove"
                onClick={() => removeArtifact(artifact.id)}
                aria-label={`Remove ${artifact.name}`}
              >
                <X size={15} strokeWidth={2.2} />
              </button>
            </div>
          ))}
        </div>
      )}

      {skippedCount > 0 && (
        <p className="form-hint">
          Skipped {skippedCount} file{skippedCount === 1 ? '' : 's'} of unsupported types.
        </p>
      )}

      <UploaderHints artifacts={artifacts} capabilities={capabilities} />
    </div>
  );
}

function PasteBox({ onAdd }) {
  const [text, setText] = useState('');
  return (
    <div className="paste-box">
      <textarea
        className="uploader-textarea"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Paste your requirements here..."
        rows="6"
      />
      <button
        type="button"
        className="button button-secondary"
        disabled={!text.trim()}
        onClick={() => onAdd(text)}
      >
        <Plus size={14} strokeWidth={2.4} /> Add as artifact
      </button>
    </div>
  );
}

function UploaderHints({ artifacts, capabilities }) {
  if (!capabilities) return null;

  const untyped = artifacts.some((artifact) => !artifact.kind);
  const sides = resolveSides(artifacts, capabilities);

  const oversized = artifacts.reduce(
    (sum, artifact) => sum + artifact.entries.reduce((n, entry) => n + entry.file.size, 0),
    0,
  ) > capabilities.max_total_upload_bytes;

  const labelFor = (kind) => findKind(capabilities, kind)?.label ?? kind;

  return (
    <>
      {untyped && <p className="form-hint warn">Choose an artifact type for each upload.</p>}
      {/* Any kind can sit on either side, so the hint asks which side is empty
          rather than naming one particular type. */}
      {artifacts.length > 0 && !sides.selectedSources.length && (
        <p className="form-hint warn">Set one artifact to <b>Trace from</b>.</p>
      )}
      {artifacts.length > 0 && !sides.selectedTargets.length && (
        <p className="form-hint warn">Set one artifact to <b>Trace to</b>.</p>
      )}
      {sides.mixedSides.map((side) => (
        <p className="form-hint warn" key={side}>
          The <b>{side === 'source' ? 'Trace from' : 'Trace to'}</b> side mixes{' '}
          {(side === 'source' ? sides.sourceKinds : sides.targetKinds).map(labelFor).join(' and ')}{' '}
          artifacts. One side takes one artifact type.
        </p>
      ))}
      {oversized && (
        <p className="form-hint warn">
          Total upload exceeds {Math.round(capabilities.max_total_upload_bytes / 1024 / 1024)} MB.
        </p>
      )}
    </>
  );
}
