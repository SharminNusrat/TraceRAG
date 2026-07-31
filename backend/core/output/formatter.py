from core.schemas import Element
from core.output.trace_link import TraceLink, ConfidenceLevel

class TraceMatrix:

    def __init__(self, source_elements: list[Element], target_elements: list[Element], trace_links: list[TraceLink]):
        self.source_elements = [e for e in source_elements if e.compare]
        self.target_elements = [e for e in target_elements if e.compare]
        self.trace_links = trace_links
        self._link_map = {(link.source_id, link.target_id): link for link in trace_links}
        # Unfiltered lookups: aggregation may roll a link up to a coarser element
        # (a class or file) that itself has compare=False.
        self._content_map = {e.identifier: e.content for e in source_elements + target_elements}

    def get_link(self, source_id: str, target_id: str) -> TraceLink | None:
        return self._link_map.get((source_id, target_id))

    def get_unimplemented(self) -> list[Element]:
        linked_sources = {link.source_id for link in self.trace_links}
        return [e for e in self.source_elements if e.identifier not in linked_sources]

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