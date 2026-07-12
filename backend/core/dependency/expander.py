from core.classification.base import ClassificationResult
from core.dependency.base import CodeDependency, DependencyType
from core.dependency.graph import DependencyGraph


class DependencyLinkExpander:
    def __init__(
        self,
        graph: DependencyGraph,
        max_depth: int = 1,
        confidence_decay: float = 0.85,
        dependency_types: set[DependencyType] | None = None,
        min_edge_confidence: float = 0.70,
        min_expanded_confidence: float = 0.35,
        min_target_granularity: int | None = None,
    ):
        self.graph = graph
        self.max_depth = max_depth
        self.confidence_decay = confidence_decay
        self.min_edge_confidence = min_edge_confidence
        self.min_expanded_confidence = min_expanded_confidence
        self.min_target_granularity = min_target_granularity
        self.dependency_types = dependency_types or {
            DependencyType.CALLS,
            DependencyType.INSTANTIATES,
            DependencyType.EXTENDS,
            DependencyType.IMPLEMENTS,
        }

    def expand(self, results: list[ClassificationResult]) -> list[ClassificationResult]:
        expanded_results = list(results)
        seen = {(result.source.identifier, result.target.identifier) for result in results}

        for result in results:
            related_paths = self.graph.expand_paths(
                seed_ids=[result.target.identifier],
                max_depth=self.max_depth,
                dependency_types=self.dependency_types,
                min_confidence=self.min_edge_confidence,
                include_incoming=False,
            )

            for related_id, path in related_paths.items():
                if related_id == result.target.identifier:
                    continue
                if related_id not in self.graph.nodes:
                    continue

                related_target = self.graph.nodes[related_id]
                if not related_target.compare:
                    continue
                if (
                    self.min_target_granularity is not None
                    and related_target.granularity < self.min_target_granularity
                ):
                    continue

                pair = (result.source.identifier, related_id)
                if pair in seen:
                    continue

                confidence = self._expanded_confidence(result.confidence, path)
                if confidence < self.min_expanded_confidence:
                    continue

                seen.add(pair)
                expanded_results.append(ClassificationResult(
                    source=result.source,
                    target=related_target,
                    confidence=confidence,
                    explanation=self._explanation(result, path),
                ))

        return expanded_results

    def _expanded_confidence(self, base_confidence: float, path: list[CodeDependency]) -> float:
        edge_confidence = 1.0
        for edge in path:
            edge_confidence *= edge.confidence
        return base_confidence * edge_confidence * (self.confidence_decay ** len(path))

    def _explanation(self, result: ClassificationResult, path: list[CodeDependency]) -> str:
        steps = " -> ".join(
            f"{edge.dependency_type.value} ({self._evidence(edge)})"
            for edge in path
        )
        graph_explanation = (
            f"Dependency expansion from {result.target.identifier}: {steps}."
        )
        return f"{result.explanation} {graph_explanation}" if result.explanation else graph_explanation

    @staticmethod
    def _evidence(edge: CodeDependency) -> str:
        evidence = (edge.evidence or edge.target_name or edge.target_id or "resolved symbol")
        return " ".join(evidence.split())[:120]
