import { useState } from 'react';
import { Download, FileText, FolderCode } from 'lucide-react';
import { downloadArtifact, formatBytes } from '../api/projectsApi';

const ICONS = { requirements: FileText, code: FolderCode };

/**
 * The stored inputs of a reopened analysis, with a way to pull them back down.
 * Only rendered for a saved run - a live one still has its files in the
 * browser and has nothing stored yet.
 */
export function ArtifactStrip({ artifacts, savedAs }) {
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  if (!artifacts?.length) return null;

  const download = async (artifact) => {
    setError(null);
    setBusyId(artifact.artifact_id);
    try {
      await downloadArtifact(artifact);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="artifact-strip">
      <header>
        <b>Analyzed Artifacts</b>
        {savedAs && <span>{savedAs}</span>}
      </header>

      <div className="artifact-items">
        {artifacts.map((artifact) => {
          const Icon = ICONS[artifact.artifact_type] ?? FileText;
          return (
            <article key={artifact.artifact_id}>
              <span className="artifact-icon"><Icon size={15} strokeWidth={2} /></span>
              <div>
                <b>{artifact.name}</b>
                <small>
                  {artifact.role} · {artifact.file_count} file
                  {artifact.file_count === 1 ? '' : 's'} · {formatBytes(artifact.byte_size)}
                </small>
              </div>
              <button
                type="button"
                onClick={() => download(artifact)}
                disabled={busyId === artifact.artifact_id}
                aria-label={`Download ${artifact.name}`}
              >
                <Download size={14} strokeWidth={2} />
                {busyId === artifact.artifact_id ? 'Preparing…' : 'Download'}
              </button>
            </article>
          );
        })}
      </div>

      {error && <p className="auth-error" role="alert">{error}</p>}
    </section>
  );
}
