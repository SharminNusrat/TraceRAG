/**
 * Shaped exactly like a raw AnalyzeResponse from the API, so it stays a valid
 * input to normalizeResult() and keeps the results page renderable offline.
 */
export const mockResult = {
  summary: {
    total_source_elements: 18,
    total_target_elements: 124,
    total_links: 3,
    unimplemented_count: 2,
    high_confidence: 2,
    medium_confidence: 1,
    low_confidence: 0,
  },
  unimplemented: [
    { identifier: 'srs::4.7', content: '4.7 Audit Export\nThe system shall export an audit trail.' },
    { identifier: 'srs::4.8', content: '4.8 Data Retention\nRecords shall be purged after 90 days.' },
  ],
  trace_links: [
    {
      source_id: 'srs::3.1',
      source_content: '3.1 User Authentication\nUsers can authenticate with email and password.',
      target_id: 'src/auth/login.ts::AuthService::signIn(email, password)',
      target_content: 'async signIn(email, password) { /* ... */ }',
      confidence: 0.94,
      confidence_level: 'high',
      explanation: 'Authentication service and validation logic directly implement this requirement.',
    },
    {
      source_id: 'srs::3.2',
      source_content: '3.2 Project Workspaces\nThe system shall create a new project workspace.',
      target_id: 'src/projects/createProject.ts::createProject(owner, name)',
      target_content: 'export function createProject(owner, name) { /* ... */ }',
      confidence: 0.88,
      confidence_level: 'high',
      explanation: 'Project creation flow maps to the workspace requirement.',
    },
    {
      source_id: 'srs::3.3',
      source_content: '3.3 Analysis History\nUsers can review previous analysis results.',
      target_id: 'src/history/results.tsx::ResultsView::render()',
      target_content: 'render() { return <ResultsTable rows={this.props.rows} />; }',
      confidence: 0.76,
      confidence_level: 'medium',
      explanation: 'The results page exposes saved analysis history.',
    },
  ],
};
