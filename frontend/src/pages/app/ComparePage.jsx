import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, Minus, Pencil, Plus } from 'lucide-react';
import { PageHeader } from '../../components/common/PageHeader';
import {
  runTimestamp,
  compareAnalyses,
  relativeTime,
} from '../../features/projects/api/projectsApi';

const SETTING_LABELS = {
  source_preprocessor: 'Source split into',
  target_preprocessor: 'Target split into',
  source_output_level: 'Source links reported at',
  target_output_level: 'Target links reported at',
  classifier_type: 'Classifier',
  top_k: 'Top-K retrieval',
  dependency_expansion_depth: 'Dependency expansion',
  summarize_elements: 'Summarize before embedding',
};

/** Booleans arrive as "True"/"False" from the server's string rendering. */
const settingValue = (value) => {
  if (value === null || value === undefined) return '—';
  if (value === 'True' || value === 'False') return value === 'True' ? 'On' : 'Off';
  return value;
};

const FIELD_LABELS = {
  similarity_score: 'score',
  confidence_level: 'confidence band',
  explanation: 'explanation',
};

function LinkRow({ link, tone }) {
  return (
    <article className={`diff-row ${tone}`}>
      <div className="diff-ids">
        <b>{link.source_id}</b>
        <i>→</i>
        <code>{link.target_id}</code>
      </div>
      <span className={`matrix-score ${link.confidence_level}`}>
        {link.similarity_score.toFixed(2)}
      </span>
    </article>
  );
}

function ModifiedRow({ link }) {
  const scoreMoved = link.changed_fields.includes('similarity_score');
  return (
    <article className="diff-row modified">
      <div className="diff-ids">
        <b>{link.source_id}</b>
        <i>→</i>
        <code>{link.target_id}</code>
        <small>
          changed: {link.changed_fields.map((f) => FIELD_LABELS[f] ?? f).join(', ')}
        </small>
      </div>
      {scoreMoved ? (
        <span className="diff-scores">
          <span className={`matrix-score ${link.base_confidence_level}`}>
            {link.base_similarity_score.toFixed(2)}
          </span>
          <i>→</i>
          <span className={`matrix-score ${link.head_confidence_level}`}>
            {link.head_similarity_score.toFixed(2)}
          </span>
        </span>
      ) : (
        <span className={`matrix-score ${link.head_confidence_level}`}>
          {link.head_similarity_score.toFixed(2)}
        </span>
      )}
    </article>
  );
}

function Section({ icon: Icon, title, tone, items, render }) {
  if (!items.length) return null;
  return (
    <section className="diff-section">
      <header className={tone}>
        <Icon size={14} strokeWidth={2.4} />
        <b>{title}</b>
        <span>{items.length}</span>
      </header>
      {items.map(render)}
    </section>
  );
}

export function ComparePage() {
  const { baseId, headId } = useParams();
  const [diff, setDiff] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    compareAnalyses(baseId, headId)
      .then((data) => { if (active) setDiff(data); })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [baseId, headId]);

  if (error) {
    return (
      <>
        <PageHeader title="Compare Runs" />
        <p className="auth-error" role="alert">{error}</p>
        <Link className="row-open" to="/app/history">← Back to history</Link>
      </>
    );
  }

  if (!diff) return <p className="dialog-note">Loading comparison…</p>;

  const { summary, base, head } = diff;
  const noChanges = !summary.added && !summary.removed && !summary.modified;

  return (
    <>
      <PageHeader
        title="Compare Runs"
        description={`${base.project_name} · what changed between two saved analyses.`}
        actions={(
          <Link className="row-open" to="/app/history">
            <ArrowLeft size={13} strokeWidth={2} /> History
          </Link>
        )}
      />

      <section className="compare-heads">
        <article>
          <small>Before</small>
          <b>{runTimestamp(base)}</b>
          <span>{base.link_count} links · {relativeTime(base.created_at)}</span>
        </article>
        <i>→</i>
        <article>
          <small>After</small>
          <b>{runTimestamp(head)}</b>
          <span>{head.link_count} links · {relativeTime(head.created_at)}</span>
        </article>
      </section>

      {!diff.comparable && (
        <p className="compare-warning" role="alert">
          <AlertTriangle size={15} strokeWidth={2.2} />
          <span>
            These runs report at different granularities, so their identifiers do not
            line up — nearly every link will appear both added and removed. Compare
            runs that share the same preprocessors and output levels for a meaningful diff.
          </span>
        </p>
      )}

      <section className="result-stats">
        {[
          ['Added', summary.added],
          ['Removed', summary.removed],
          ['Modified', summary.modified],
          ['Unchanged', summary.unchanged],
        ].map(([label, value]) => (
          <div key={label}>
            <b>{value}</b>
            <span className="result-stat-label">{label}</span>
          </div>
        ))}
      </section>

      {Boolean(diff.config_differences.length) && (
        <section className="compare-settings">
          <header><b>Settings That Differ</b></header>
          {diff.config_differences.map((row) => (
            <div key={row.setting}>
              <span>{SETTING_LABELS[row.setting] ?? row.setting}</span>
              <code>{settingValue(row.base)}</code>
              <i>→</i>
              <code>{settingValue(row.head)}</code>
            </div>
          ))}
        </section>
      )}

      {Boolean(diff.newly_implemented.length || diff.newly_unimplemented.length) && (
        <section className="compare-coverage">
          {Boolean(diff.newly_implemented.length) && (
            <div className="added">
              <b>Now Implemented</b>
              <p>{diff.newly_implemented.join(', ')}</p>
            </div>
          )}
          {Boolean(diff.newly_unimplemented.length) && (
            <div className="removed">
              <b>No Longer Implemented</b>
              <p>{diff.newly_unimplemented.join(', ')}</p>
            </div>
          )}
        </section>
      )}

      {noChanges && (
        <p className="dialog-note">
          These two runs recovered exactly the same {summary.unchanged} links.
        </p>
      )}

      <Section
        icon={Plus} title="Added" tone="added" items={diff.added}
        render={(l) => <LinkRow key={`${l.source_id}|${l.target_id}`} link={l} tone="added" />}
      />
      <Section
        icon={Minus} title="Removed" tone="removed" items={diff.removed}
        render={(l) => <LinkRow key={`${l.source_id}|${l.target_id}`} link={l} tone="removed" />}
      />
      <Section
        icon={Pencil} title="Modified" tone="modified" items={diff.modified}
        render={(l) => <ModifiedRow key={`${l.source_id}|${l.target_id}`} link={l} />}
      />
    </>
  );
}
