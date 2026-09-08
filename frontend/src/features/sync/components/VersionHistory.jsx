import { useEffect, useState } from 'react';
import { listVersions, relativeTime } from '../../projects/api/projectsApi';
import { shortRef } from '../api/syncApi';

/**
 * Every state a project's artifacts have been in, and what moved between them.
 *
 * A version records what *every* source was, not only the ones that were
 * refreshed - so comparing two rows says which parts actually changed, without
 * reading a single file.
 */
export function VersionHistory({ projectId }) {
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    listVersions(projectId)
      .then((rows) => { if (active) setVersions(rows); })
      .catch((requestError) => {
        if (active) { setVersions([]); setError(requestError.message); }
      });
    return () => { active = false; };
  }, [projectId]);

  if (versions === null) return <p className="dialog-note">Loading versions…</p>;
  if (error) return <p className="auth-error" role="alert">{error}</p>;
  if (!versions.length) {
    return (
      <p className="dialog-note">
        No versions yet. Saving an analysis records the state its artifacts were in.
      </p>
    );
  }

  /** Which sources differ from the version below this one in the list. */
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
                  <span className="version-source muted">No sources recorded</span>
                )}
              </div>
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
