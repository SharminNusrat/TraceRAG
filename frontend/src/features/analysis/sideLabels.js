/**
 * What to call the things on each side of a trace.
 *
 * Neither side is fixed to a kind any more - a run can be requirements to code,
 * documentation to an architecture model, or anything else the backend accepts.
 * So the wording is read off the elements themselves rather than hardcoded:
 * whatever level the links are reported at is what the results view calls them.
 */

const LEVEL_NOUNS = {
  artifact: ['Document', 'Documents'],
  section: ['Section', 'Sections'],
  sentence: ['Sentence', 'Sentences'],
  package: ['Package', 'Packages'],
  file: ['File', 'Files'],
  class: ['Class', 'Classes'],
  function: ['Method', 'Methods'],
  chunk: ['Code chunk', 'Code chunks'],
  component: ['Component', 'Components'],
  interface: ['Interface', 'Interfaces'],
};

const FALLBACK = { source: ['Source element', 'Source elements'], target: ['Target element', 'Target elements'] };

/** The level most of a side's elements sit at - links are reported at one level. */
function dominantLevel(elements) {
  const counts = new Map();
  for (const element of elements ?? []) {
    counts.set(element.level, (counts.get(element.level) ?? 0) + 1);
  }

  let best = null;
  let bestCount = 0;
  for (const [level, count] of counts) {
    if (count > bestCount) {
      best = level;
      bestCount = count;
    }
  }
  return best;
}

export function sideLabels(elements, role) {
  const [singular, plural] = LEVEL_NOUNS[dominantLevel(elements)] ?? FALLBACK[role];
  return { singular, plural, lower: singular.toLowerCase(), lowerPlural: plural.toLowerCase() };
}

/** True when a side is an architecture model: only those carry model relations. */
export const isArchitecture = (elements) =>
  Boolean(elements?.some((element) => element.model_units));
