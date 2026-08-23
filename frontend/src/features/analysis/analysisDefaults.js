/**
 * `sourcePreprocessor`/`targetPreprocessor` control how artifacts are *split*.
 * `sourceOutputLevel`/`targetOutputLevel` control the level recovered links are
 * *reported* at - two different things that used to share the name "granularity".
 * Both output levels start null, meaning "leave links where the classifier put
 * them"; the settings step fills them from the backend's defaults.
 */
export const defaultAnalysisDraft = {
  // Which artifact sits on which side is carried on the artifact itself, set
  // in the upload step - any kind can be either side, so the file type cannot
  // decide it.
  sourcePreprocessor: 'section',
  targetPreprocessor: 'method',
  sourceOutputLevel: null,
  targetOutputLevel: null,
  // Overwritten from GET /capabilities once it loads - the backend's
  // AnalyzeRequest is the source of truth for these.
  classifier: 'reasoning',
  nResults: 10,
  dependencyExpansionDepth: 0,
  summarizeElements: true,
  analysisMode: 'session',
};
