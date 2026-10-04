import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { listVersions, relativeTime, runTimestamp } from '../../projects/api/projectsApi';
import { shortRef } from '../api/syncApi';

/**
 * Every state an analysis's files have been in, and what moved between them.
 *
 * A version records what *both* sides were, not only the one that was
 * updated - so comparing two rows says which side actually changed, without
 * reading a single file. Given `onOpenRun`, each version also lists its runs.
 */
export function VersionHistory({ projectId, configId, onOpenRun, onDeleteRun }) {
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);
  // The run whose delete was refused: a version keeps its last run.
  const [refused, setRefused] = useState(null);

  useEffect(() => {
    let active = true;
    setVersions(null);
    listVersions(projectId, configId)
      .then((rows) => { if (active) setVersions(rows); })
      .catch((requestError) => {
        if (active) { setVersions([]); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId, configId]);

  if (versions === null) return <p className="dialog-note">Loading versions…</p>;
  if (error) return <p className="auth-error" role="alert">{error}</p>;
  if (!versions.length) {
    return (
      <p className="dialog-note">
        No versions yet. Saving an analysis records the state its artifacts were in.
      </p>
    );
  }

  /** Which sides differ from the version below this one in the list. */
  const movedIn = (index) => {
    // Newest first, so the previous state is the next row down. The oldest has
    // nothing before it: everything in it arrived at once.
    const older = versions[index + 1];
    if (!older) return null;
    const before = new Map(older.sources.map((source) => [source.source_id, source.ref]));
    return new Set(
      versions[index].sources
        .filter((source) => before.get(source.source_id) !== source.ref)
        .map((source) => source.source_id),
    );
  };

  return (
    <div className="version-list">
      {versions.map((version, index) => {
        const moved = movedIn(index);
        return (
          <article className="version-row" key={version.version_id}>
            <div className="version-mark">
              <b>v{version.version_number}</b>
              <small>{relativeTime(version.created_at)}</small>
            </div>
            <div className="version-body">
              {version.note && <p className="analysis-note">{version.note}</p>}
              <div className="version-sources">
                {version.sources.map((source) => {
                  const changed = moved?.has(source.source_id);
                  return (
                    <span
                      key={source.source_id}
                      className={changed ? 'version-source changed' : 'version-source'}
                      title={source.ref}
                    >
                      <b>{source.name}</b>
                      <code>{shortRef(source.ref)}</code>
                      {changed && <em>changed</em>}
                    </span>
                  );
                })}
                {!version.sources.length && (
                  <span className="version-source muted">No files recorded</span>
                )}
              </div>
              {onOpenRun && version.runs.map((run) => (
                <div className="version-run" key={run.analysis_id}>
                  <span>{runTimestamp(run)} · {run.link_count} trace links</span>
                  {run.note && <em>{run.note}</em>}
                  <button type="button" className="row-open" onClick={() => onOpenRun(run.analysis_id)}>
                    Open
                  </button>
                  {onDeleteRun && (
                    <button
                      type="button"
                      className="card-delete"
                      onClick={() => (version.runs.length === 1
                        ? setRefused(run.analysis_id)
                        : onDeleteRun(run))}
                      aria-label={`Delete ${runTimestamp(run)}`}
                    >
                      <Trash2 size={13} strokeWidth={2} />
                    </button>
                  )}
                  {refused === run.analysis_id && (
                    <em className="sync-problem" role="alert">This is the only run of this version.</em>
                  )}
                </div>
              ))}
            </div>
            <span className="version-runs">
              {version.analysis_count} run{version.analysis_count === 1 ? '' : 's'}
            </span>
          </article>
        );
      })}
    </div>
  );
}
