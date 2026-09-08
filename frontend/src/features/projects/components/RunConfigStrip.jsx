import { Fragment, useState } from 'react';
import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { findKind, findPreprocessor, useCapabilities } from '../../analysis/api/capabilitiesApi';

/**
 * The settings a result was produced with.
 *
 * Which preprocessor split the artifacts, and at which level links were
 * reported, decide what the identifiers in the result even refer to - so a
 * reopened analysis is not readable without them. They were only ever visible
 * inside the re-run dialog, which is the wrong place: by then the question is
 * what to change, not what was used.
 */
export function RunConfigStrip({ config, sourceKind, targetKind }) {
  const { capabilities } = useCapabilities();
  // Collapsed by default. The summary line answers the question most of the
  // time; the grid is for when two runs disagree and the reason matters.
  const [open, setOpen] = useState(false);

  if (!config) return null;

  /** One side, resolved from keys to the labels the settings step offered. */
  const side = (kindKey, preprocessorField, levelField) => {
    const kind = findKind(capabilities, kindKey);
    const preprocessor = findPreprocessor(kind, config[preprocessorField]);
    const level = preprocessor?.output_levels.find((l) => l.key === config[levelField]);
    return {
      // Fall back to the stored key rather than showing nothing: an artifact
      // kind that is no longer supported still has to say what it was.
      kind: kind?.label ?? kindKey ?? 'Unknown',
      splitInto: preprocessor?.label ?? config[preprocessorField] ?? '—',
      reportedAt: level?.label ?? config[levelField] ?? 'As classified',
    };
  };

  const source = side(sourceKind, 'source_preprocessor', 'source_output_level');
  const target = side(targetKind, 'target_preprocessor', 'target_output_level');

  const classifier = capabilities?.classifiers
    .find((option) => option.key === config.classifier);

  const depth = config.dependency_expansion_depth;
  const run = [
    ['Classifier', classifier?.label ?? config.classifier],
    ['Candidates per source element', config.n_results],
    ['Dependency expansion', depth > 0 ? `Depth ${depth}` : 'Off'],
    ['Summarize before embedding', config.summarize_elements ? 'On' : 'Off'],
  ];

  return (
    <section className="run-config">
      <button
        type="button"
        className="run-config-head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="run-config-icon"><SlidersHorizontal size={15} strokeWidth={2} /></span>
        <div>
          <b>Run Configuration</b>
          <small>
            {source.kind} ({source.splitInto}) → {target.kind} ({target.splitInto})
            {' · '}{classifier?.label ?? config.classifier}
          </small>
        </div>
        <ChevronDown
          size={16}
          strokeWidth={2}
          className={open ? 'run-config-chevron open' : 'run-config-chevron'}
        />
      </button>

      {open && (
        <div className="run-config-body">
          {[['Source', source], ['Target', target]].map(([role, values]) => (
            <article key={role}>
              <h4>{role} · {values.kind}</h4>
              <dl>
                <dt>Split into</dt><dd>{values.splitInto}</dd>
                <dt>Links reported at</dt><dd>{values.reportedAt}</dd>
              </dl>
            </article>
          ))}
          <article>
            <h4>Run</h4>
            <dl>
              {run.map(([label, value]) => (
                <Fragment key={label}><dt>{label}</dt><dd>{value}</dd></Fragment>
              ))}
            </dl>
          </article>
        </div>
      )}
    </section>
  );
}
