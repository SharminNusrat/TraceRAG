from core.schemas import Element
from core.classification.base import ClassificationResult
from core.output.trace_link import TraceLink

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
        
        trace_links = []
        seen = set()

        for result in classification_results:
            valid_sources = self._get_valid_elements(result.source, self.source_granularity, source_elements)
            valid_targets = self._get_valid_elements(result.target, self.target_granularity, target_elements)

            for source in valid_sources:
                for target in valid_targets:
                    pair = (source.identifier, target.identifier)
                    if pair not in seen:
                        seen.add(pair)
                        trace_links.append(TraceLink(
                            source_id=source.identifier,
                            target_id=target.identifier,
                            confidence=result.confidence,
                            confidence_level=TraceLink.confidence_to_level(result.confidence),
                            explanation=result.explanation
                        ))
        return trace_links

    def _get_valid_elements(self, element: Element, desired_granularity: int, all_elements: list[Element]) -> list[Element]:
        if element.granularity == desired_granularity:
            return [element]
        elif element.granularity < desired_granularity:
            return [e for e in all_elements if e.granularity == desired_granularity and self._is_transitive_child(e, element, all_elements)]
        else:
            parents = [e for e in all_elements if e.granularity == desired_granularity and self._is_transitive_child(element, e, all_elements)]
            return parents[:1]

    def _is_transitive_child(self, possible_child: Element, parent: Element, all_elements: list[Element]) -> bool:
        current = possible_child
        visited = set()
        while current is not None:
            if current.identifier in visited:
                break
            visited.add(current.identifier)
            if current.identifier == parent.identifier:
                return True
            current = next((e for e in all_elements if e.identifier == current.parent_id), None)
        return False