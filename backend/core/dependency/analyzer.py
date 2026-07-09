import logging
from core.dependency.analyzers import get_dependency_analyzer_for_path
from core.dependency.graph import DependencyGraph
from core.dependency.resolver import DependencyResolver
from core.schemas import Artifact, Element


logger = logging.getLogger(__name__)


class CodeDependencyAnalyzer:
    def analyze(self, artifacts: list[Artifact], elements: list[Element]) -> DependencyGraph:
        graph = DependencyGraph(elements)
        resolver = DependencyResolver(elements)

        for artifact in artifacts:
            analyzer = get_dependency_analyzer_for_path(artifact.identifier)
            if analyzer is None:
                continue

            try:
                dependencies = analyzer.analyze(artifact)
            except Exception as exc:
                logger.warning(f"Dependency analysis skipped for {artifact.identifier}: {exc}")
                continue

            graph.add_edges(resolver.resolve_all(dependencies))

        return graph
