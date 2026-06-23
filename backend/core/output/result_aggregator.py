import logging
from core.schemas import Element
from core.classification.base import ClassificationResult
from core.output.trace_link import TraceLink


logger = logging.getLogger(__name__)


class ResultAggregator:

    def __init__(self, source_granularity: int = 0, target_granularity: int = 0):
        self.source_granularity = source_granularity
        self.target_granularity = target_granularity

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
            valid_source = self._get_valid_element(result.source, self.source_granularity, source_map)
            valid_target = self._get_valid_element(result.target, self.target_granularity, target_map)

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

    def _get_valid_element(self, element: Element, desired_granularity: int, element_map: dict[str, Element]) -> Element:
        if element.granularity == desired_granularity: # Exact match
            return element
        
        elif element.granularity > desired_granularity: # Element is finer than desired -> walk up ancestor chain
            current = element
            visited = set()
            while current is not None:
                if current.identifier in visited:
                    break
                visited.add(current.identifier)
                if current.granularity == desired_granularity:
                    return current
                current = element_map.get(current.parent_id)

            logger.warning(f"No ancestor of {element.identifier} matches desired granularity {desired_granularity}. Returning original element.")
            return element

        else: # Element is coarser than desired
            logger.warning(
                f"Element '{element.identifier}' granularity {element.granularity} "
                f"is coarser than desired {desired_granularity}. Expansion skipped."
            )
            return element

    # def _is_transitive_child(self, possible_child: Element, parent: Element, all_elements: list[Element]) -> bool:
    #     current = possible_child
    #     visited = set()
    #     while current is not None:
    #         if current.identifier in visited:
    #             break
    #         visited.add(current.identifier)
    #         if current.identifier == parent.identifier:
    #             return True
    #         current = next((e for e in all_elements if e.identifier == current.parent_id), None)
    #     return False