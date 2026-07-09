from core.classification.base import ClassificationResult
from core.dependency.base import DependencyType
from core.dependency.graph import DependencyGraph


class DependencyLinkExpander:
    def __init__(
        self,
        graph: DependencyGraph,
        max_depth: int = 1,
        confidence_decay: float = 0.75,
        dependency_types: set[DependencyType] | None = None
    ):
        self.graph = graph
        self.max_depth = max_depth
        self.confidence_decay = confidence_decay
        self.dependency_types = dependency_types or {
            DependencyType.CALLS,
            DependencyType.INSTANTIATES,
            DependencyType.EXTENDS,
            DependencyType.IMPLEMENTS,
            DependencyType.FIELD_TYPE,
            DependencyType.PARAM_TYPE,
            DependencyType.RETURN_TYPE,
        }

    def expand(self, results: list[ClassificationResult]) -> list[ClassificationResult]:
        expanded_results = list(results)
        seen = {(result.source.identifier, result.target.identifier) for result in results}

        for result in results:
            related_ids = self.graph.expand(
                seed_ids=[result.target.identifier],
                max_depth=self.max_depth,
                dependency_types=self.dependency_types,
                include_incoming=True
            )

            for related_id in related_ids:
                if related_id == result.target.identifier:
                    continue
                if related_id not in self.graph.nodes:
                    continue

                pair = (result.source.identifier, related_id)
                if pair in seen:
                    continue

                seen.add(pair)
                expanded_results.append(ClassificationResult(
                    source=result.source,
                    target=self.graph.nodes[related_id],
                    confidence=result.confidence * self.confidence_decay,
                    explanation=f"Expanded from {result.target.identifier} through dependency graph."
                ))

        return expanded_results
