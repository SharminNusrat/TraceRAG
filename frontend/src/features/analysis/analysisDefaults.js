/**
 * `sourcePreprocessor`/`targetPreprocessor` control how artifacts are *split*.
 * `sourceOutputLevel`/`targetOutputLevel` control the level recovered links are
 * *reported* at - two different things that used to share the name "granularity".
 * Both output levels start null, meaning "leave links where the classifier put
 * them"; the settings step fills them from the backend's defaults.
 */
export const defaultAnalysisDraft = {
  // null = analyse every source artifact together as one corpus. An artifact
  // id here narrows the trace to just that artifact. The target side is always
  // grouped, so it has no equivalent setting.
  sourceArtifactId: null,
  sourcePreprocessor: 'section',
  targetPreprocessor: 'method',
  sourceOutputLevel: null,
  targetOutputLevel: null,
  // Overwritten from GET /capabilities once it loads - the backend's
  // AnalyzeRequest is the source of truth for these.
  classifier: 'reasoning',
  nResults: 10,
  dependencyExpansionDepth: 0,
  analysisMode: 'session',
};
