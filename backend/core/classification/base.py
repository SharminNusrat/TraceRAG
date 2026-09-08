import logging
from abc import ABC, abstractmethod
from pydantic import BaseModel
from core.cache import PersistentClassificationCache, question
from core.schemas import Element

logger = logging.getLogger(__name__)

# What the model decided: whether the pair is linked, and why, when it says.
Verdict = tuple[bool, str | None]


class ClassificationResult(BaseModel):
    source: Element
    target: Element
    confidence: float
    explanation: str | None = None


class Classifier(ABC):
    """Decides which retrieved candidates a source element actually traces to.

    Asking the model is the subclass's job. Deciding what is still worth
    asking is this class's, because both classifiers ask the same question of
    the same pairs and pay the same price for the answer.
    """

    def __init__(self, cache_namespace: str | None = None):
        # Given a namespace only when answers should outlive the run. Without
        # one the classifier behaves exactly as it did before: every pair asked.
        self._cache = (
            PersistentClassificationCache(cache_namespace) if cache_namespace else None
        )

    def classify(
        self, source: Element, target_candidates: list[tuple[Element, float]]
    ) -> list[ClassificationResult]:
        verdicts = self._verdicts(source, [target for target, _ in target_candidates])

        results = []
        for target, similarity in target_candidates:
            linked, explanation = verdicts[target.identifier]
            if linked:
                # The score is the retrieval similarity, not the model's - it
                # answers yes or no and nothing in between.
                results.append(ClassificationResult(
                    source=source,
                    target=target,
                    confidence=similarity,
                    explanation=explanation,
                ))
        return results

    def _verdicts(self, source: Element, targets: list[Element]) -> dict[str, Verdict]:
        """One verdict per target, asking the model only where none is known."""
        asked = {
            target.identifier: question(source.type, source.content, target.type, target.content)
            for target in targets
        }
        # Two candidates with identical text are one question, so the store is
        # read and written by question rather than by identifier.
        distinct = set(asked.values())
        known = self._cache.get_many(list(distinct)) if self._cache else {}

        fresh: dict[str, Verdict] = {}
        verdicts: dict[str, Verdict] = {}
        for target in targets:
            text = asked[target.identifier]
            if text in known:
                verdicts[target.identifier] = known[text]
                continue
            if text not in fresh:
                fresh[text] = self._ask(source, target)
            verdicts[target.identifier] = fresh[text]

        # The model call is the expensive step, so say how many were actually
        # made rather than leaving a cached run and a paid one looking alike.
        logger.info(
            f"  classifier: {len(distinct) - len(fresh)}/{len(distinct)} cached, "
            f"{len(fresh)} model call(s)"
        )

        if self._cache and fresh:
            self._cache.set_many(fresh)
        return verdicts

    @abstractmethod
    def _ask(self, source: Element, target: Element) -> Verdict:
        """One model call, for one pair."""
