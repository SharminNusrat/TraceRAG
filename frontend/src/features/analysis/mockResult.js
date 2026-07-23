export const mockResult = {
  summary: {
    requirements: 18,
    trace_links: 46,
    high_confidence: 39,
  },
  unimplemented: [{ source_id: 'REQ-17' }, { source_id: 'REQ-18' }],
  trace_links: [
    {
      source_id: 'REQ-01',
      target_id: 'src/auth/login.ts',
      confidence: 0.94,
      confidence_level: 'high',
      explanation: 'Authentication service and validation logic directly implement this requirement.',
    },
    {
      source_id: 'REQ-02',
      target_id: 'src/projects/createProject.ts',
      confidence: 0.88,
      confidence_level: 'high',
      explanation: 'Project creation flow maps to the workspace requirement.',
    },
    {
      source_id: 'REQ-03',
      target_id: 'src/history/results.tsx',
      confidence: 0.76,
      confidence_level: 'medium',
      explanation: 'The results page exposes saved analysis history.',
    },
  ],
};
