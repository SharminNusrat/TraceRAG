import { PageHeader } from '../../components/common/PageHeader';

const analyses = [
  'Authentication coverage',
  'Release 2.4 requirements',
  'Payment workflow review',
  'Onboarding flow',
];

export function HistoryPage() {
  return (
    <>
      <PageHeader title="Analysis history" description="Saved analysis runs across all of your projects." />
      <section className="history-card">
        {analyses.map((analysis, index) => (
          <article className="analysis-row" key={analysis}>
            <span>↗</span>
            <div>
              <b>{analysis}</b>
              <small>Atlas workspace · {12 + index * 4} requirements · {36 + index * 11} trace links</small>
            </div>
            <em className={index === 2 ? 'review' : ''}>{index === 2 ? 'Needs review' : 'Completed'}</em>
            <time>{index + 1}d ago</time>
          </article>
        ))}
      </section>
    </>
  );
}
