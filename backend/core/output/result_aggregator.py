import logging
from core.schemas import Element, ElementLevel
from core.classification.base import ClassificationResult
from core.output.trace_link import TraceLink


logger = logging.getLogger(__name__)


class ResultAggregator:
    """Rolls classified links up to the level the user asked to see."""

    def __init__(
        self,
        source_level: ElementLevel | None = None,
        target_level: ElementLevel | None = None,
    ):
        self.source_level = source_level
        self.target_level = target_level

    def aggregate(
            self,
            source_elements: list[Element],
            target_elements: list[Element],
            classification_results: list[ClassificationResult]
    ) -> list[TraceLink]:

        source_map = {e.identifier: e for e in source_elements}
        target_map = {e.identifier: e for e in target_elements}

        trace_links = []
        seen = set()

        for result in classification_results:
            valid_source = self._get_valid_element(result.source, self.source_level, source_map)
            valid_target = self._get_valid_element(result.target, self.target_level, target_map)

            pair = (valid_source.identifier, valid_target.identifier)

            if pair in seen:
                continue

            seen.add(pair)
            trace_links.append(TraceLink(
                source_id=valid_source.identifier,
                target_id=valid_target.identifier,
                confidence=result.confidence,
                confidence_level=TraceLink.confidence_to_level(result.confidence),
                explanation=result.explanation
            ))
        return trace_links

    def _get_valid_element(
        self,
        element: Element,
        desired_level: ElementLevel | None,
        element_map: dict[str, Element],
    ) -> Element:
        if desired_level is None or element.level == desired_level:
            return element

        current = element
        visited = set()
        while current is not None:
            if current.identifier in visited:
                break
            visited.add(current.identifier)
            if current.level == desired_level:
                return current
            current = element_map.get(current.parent_id)

        logger.warning(
            f"No ancestor of '{element.identifier}' (level={element.level.value}) has "
            f"level '{desired_level.value}'. Reporting the element as classified."
        )
        return element
