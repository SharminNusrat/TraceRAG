from core.schemas import Element, ElementLevel
from core.output.trace_link import TraceLink, ConfidenceLevel


class TraceMatrix:
    """The finished trace matrix, plus the element inventory behind it.

    Both artifact sides are reported in full - not only the linked elements -
    so a viewer can list every element of the source and target and show which
    ones a selected element connects to.
    """

    def __init__(
        self,
        source_elements: list[Element],
        target_elements: list[Element],
        trace_links: list[TraceLink],
        source_level: ElementLevel | None = None,
        target_level: ElementLevel | None = None,
    ):
        self.trace_links = trace_links
        self._link_map = {(link.source_id, link.target_id): link for link in trace_links}
        # Unfiltered lookups: aggregation may roll a link up to a coarser element
        # (a class or file) that itself has compare=False.
        self._content_map = {e.identifier: e.content for e in source_elements + target_elements}

        # Links are reported at the requested output level, so the inventory has
        # to be drawn at that level too - otherwise link ids would point at
        # elements the viewer never lists.
        self.source_elements = self._at_level(
            source_elements, source_level, {link.source_id for link in trace_links}
        )
        self.target_elements = self._at_level(
            target_elements, target_level, {link.target_id for link in trace_links}
        )

    @staticmethod
    def _at_level(
        elements: list[Element],
        level: ElementLevel | None,
        referenced: set[str],
    ) -> list[Element]:
        if level is None:
            selected = [e for e in elements if e.compare]
        else:
            selected = [e for e in elements if e.level == level]

        # A link whose rollup fell back to a different level still needs its
        # element listed, so union in anything the links actually reference.
        present = {e.identifier for e in selected}
        selected += [
            e for e in elements
            if e.identifier in referenced and e.identifier not in present
        ]
        return selected

    def get_link(self, source_id: str, target_id: str) -> TraceLink | None:
        return self._link_map.get((source_id, target_id))

    def get_unimplemented(self) -> list[Element]:
        linked_sources = {link.source_id for link in self.trace_links}
        return [e for e in self.source_elements if e.identifier not in linked_sources]

    @staticmethod
    def _element_dict(element: Element) -> dict:
        return {
            "identifier": element.identifier,
            "content": element.content,
            "level": element.level.value,
            "type": element.type,
            "parent_id": element.parent_id,
            # Only architecture elements carry these; everything else sends null.
            "model_units": element.model_units.model_dump() if element.model_units else None,
        }

    def to_dict(self) -> dict:
        return {
            "trace_links": [
                {
                    "source_id": link.source_id,
                    "source_content": self._content_map.get(link.source_id),
                    "target_id": link.target_id,
                    "target_content": self._content_map.get(link.target_id),
                    "confidence": link.confidence,
                    "confidence_level": link.confidence_level.value,
                    "explanation": link.explanation
                }
                for link in self.trace_links
            ],
            "source_elements": [self._element_dict(e) for e in self.source_elements],
            "target_elements": [self._element_dict(e) for e in self.target_elements],
            "unimplemented": [
                {
                    "identifier": e.identifier,
                    "content": e.content
                }
                for e in self.get_unimplemented()
            ],
            "summary": {
                "total_source_elements": len(self.source_elements),
                "total_target_elements": len(self.target_elements),
                "total_links": len(self.trace_links),
                "unimplemented_count": len(self.get_unimplemented()),
                "high_confidence": len([l for l in self.trace_links if l.confidence_level == ConfidenceLevel.HIGH]),
                "medium_confidence": len([l for l in self.trace_links if l.confidence_level == ConfidenceLevel.MEDIUM]),
                "low_confidence": len([l for l in self.trace_links if l.confidence_level == ConfidenceLevel.LOW]),
            }
        }
