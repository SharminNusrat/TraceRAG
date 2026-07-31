import { useEffect, useState } from 'react';
import { httpClient } from '../../../services/httpClient';

export const fetchCapabilities = () => httpClient('/capabilities');

/**
 * Everything the settings and uploader steps offer comes from the backend's
 * /capabilities response, so supporting a new artifact type is a backend-only
 * change. `null` while loading.
 */
export function useCapabilities() {
  const [capabilities, setCapabilities] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchCapabilities()
      .then((data) => { if (!cancelled) setCapabilities(data); })
      .catch((requestError) => { if (!cancelled) setError(requestError.message); });
    return () => { cancelled = true; };
  }, []);

  return { capabilities, error };
}

export const findKind = (capabilities, key) =>
  capabilities?.artifact_kinds.find((kind) => kind.key === key) ?? null;

export const findPreprocessor = (kind, key) =>
  kind?.preprocessors.find((preprocessor) => preprocessor.key === key) ?? null;

/** Artifacts whose kind can act as the given side of a trace. */
export function artifactsForRole(artifacts, capabilities, role) {
  if (!capabilities) return [];
  return artifacts.filter((artifact) => {
    const kind = findKind(capabilities, artifact.kind);
    return kind?.roles.includes(role);
  });
}

/**
 * Works out which artifacts form each side of the trace.
 *
 * Target is always the whole group - every code artifact is one codebase. To
 * narrow it you upload only the folder/file/zip you care about.
 * Source defaults to the same grouping, but `draft.sourceArtifactId` can pin
 * the trace to a single source artifact.
 */
export function resolveSides(artifacts, capabilities, draft) {
  const sources = artifactsForRole(artifacts, capabilities, 'source');
  const targets = artifactsForRole(artifacts, capabilities, 'target');

  const pinned = sources.find((artifact) => artifact.id === draft?.sourceArtifactId);
  const selectedSources = pinned ? [pinned] : sources;

  return {
    sources,
    targets,
    selectedSources,
    selectedTargets: targets,
    sourceIds: selectedSources.map((artifact) => artifact.id),
    targetIds: targets.map((artifact) => artifact.id),
    sourceGrouped: !pinned && sources.length > 1,
  };
}

const extensionOf = (path) => {
  const dot = path.lastIndexOf('.');
  return dot === -1 ? '' : path.slice(dot).toLowerCase();
};

/**
 * The kind a single file belongs to, or null when no kind claims it.
 * Null matters: a repository folder is full of files (package.json, README.md)
 * that no kind accepts, and the backend rejects them outright, so they are
 * dropped at selection time rather than failing the whole upload.
 */
export function kindForPath(capabilities, path) {
  if (!capabilities) return null;
  const extension = extensionOf(path);

  if (extension === '.zip') {
    return capabilities.artifact_kinds.find((kind) => kind.accepts_archive)?.key ?? null;
  }
  return capabilities.artifact_kinds.find((kind) => kind.extensions.includes(extension))?.key ?? null;
}

/** Guess an artifact kind for a set of files: whichever kind claims the most. */
export function inferKind(capabilities, entries) {
  if (!capabilities) return null;

  const counts = new Map();
  for (const entry of entries) {
    const kind = kindForPath(capabilities, entry.path);
    if (kind) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }

  let best = null;
  let bestScore = 0;
  for (const [kind, score] of counts) {
    if (score > bestScore) {
      best = kind;
      bestScore = score;
    }
  }
  return best;
}
