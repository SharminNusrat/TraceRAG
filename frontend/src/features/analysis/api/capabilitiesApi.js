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

export const SIDE_SOURCE = 'source';
export const SIDE_TARGET = 'target';
export const SIDE_UNUSED = 'unused';

/** Whether an artifact's kind is allowed to sit on the given side. */
export const canTakeSide = (capabilities, artifact, side) =>
  Boolean(findKind(capabilities, artifact?.kind)?.roles.includes(side));

/**
 * The side to put a newly added artifact on.
 *
 * Since any kind can sit on either side, this only sets a sensible starting
 * point - the uploader lets it be changed. Artifacts of a kind already on a
 * side join it, because one side is analysed as a single corpus.
 */
export function defaultSide(existing, artifact, capabilities) {
  const kindsOn = (side) => new Set(
    existing.filter((item) => item.side === side).map((item) => item.kind),
  );

  const sourceKinds = kindsOn(SIDE_SOURCE);
  if (sourceKinds.has(artifact.kind)) return SIDE_SOURCE;

  const targetKinds = kindsOn(SIDE_TARGET);
  if (targetKinds.has(artifact.kind)) return SIDE_TARGET;

  if (!sourceKinds.size && canTakeSide(capabilities, artifact, SIDE_SOURCE)) return SIDE_SOURCE;
  if (!targetKinds.size && canTakeSide(capabilities, artifact, SIDE_TARGET)) return SIDE_TARGET;
  return SIDE_UNUSED;
}

/**
 * Works out which artifacts form each side of the trace.
 *
 * The kind no longer decides: any artifact type can be either side, so the
 * assignment is the user's and is carried on the artifact itself. Everything
 * on one side is analysed together as a single corpus, which is how a set of
 * loose code files becomes one codebase - and why the backend requires each
 * side to hold a single kind.
 */
export function resolveSides(artifacts, capabilities) {
  const onSide = (side) => artifacts.filter(
    (artifact) => artifact.kind && artifact.side === side,
  );

  const selectedSources = onSide(SIDE_SOURCE);
  const selectedTargets = onSide(SIDE_TARGET);

  const kindsOf = (group) => [...new Set(group.map((artifact) => artifact.kind))];
  const sourceKinds = kindsOf(selectedSources);
  const targetKinds = kindsOf(selectedTargets);

  return {
    selectedSources,
    selectedTargets,
    sourceIds: selectedSources.map((artifact) => artifact.id),
    targetIds: selectedTargets.map((artifact) => artifact.id),
    sourceKinds,
    targetKinds,
    // The backend applies one preprocessor and one provider per side, so a
    // side holding two kinds cannot be run.
    mixedSides: [
      ...(sourceKinds.length > 1 ? ['source'] : []),
      ...(targetKinds.length > 1 ? ['target'] : []),
    ],
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
