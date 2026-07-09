from collections import defaultdict, deque
from collections.abc import Iterable

from core.dependency.base import CodeDependency, DependencyType
from core.schemas import Element


class DependencyGraph:
    def __init__(self, elements: Iterable[Element] | None = None):
        self.nodes: dict[str, Element] = {}
        self.edges: list[CodeDependency] = []
        self.outgoing: dict[str, list[CodeDependency]] = defaultdict(list)
        self.incoming: dict[str, list[CodeDependency]] = defaultdict(list)

        if elements:
            for element in elements:
                self.add_node(element)

    def add_node(self, element: Element) -> None:
        self.nodes[element.identifier] = element

    def add_edge(self, edge: CodeDependency) -> None:
        self.edges.append(edge)
        self.outgoing[edge.source_id].append(edge)
        if edge.target_id:
            self.incoming[edge.target_id].append(edge)

    def add_edges(self, edges: Iterable[CodeDependency]) -> None:
        for edge in edges:
            self.add_edge(edge)

    def get_outgoing(
        self,
        source_id: str,
        dependency_types: set[DependencyType] | None = None
    ) -> list[CodeDependency]:
        edges = self.outgoing.get(source_id, [])
        if dependency_types is None:
            return list(edges)
        return [edge for edge in edges if edge.dependency_type in dependency_types]

    def get_incoming(
        self,
        target_id: str,
        dependency_types: set[DependencyType] | None = None
    ) -> list[CodeDependency]:
        edges = self.incoming.get(target_id, [])
        if dependency_types is None:
            return list(edges)
        return [edge for edge in edges if edge.dependency_type in dependency_types]

    def expand(
        self,
        seed_ids: Iterable[str],
        max_depth: int = 1,
        dependency_types: set[DependencyType] | None = None,
        min_confidence: float = 0.0,
        include_incoming: bool = True
    ) -> set[str]:
        expanded = set(seed_ids)
        queue = deque((seed_id, 0) for seed_id in seed_ids)

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            edges = self.get_outgoing(current_id, dependency_types)
            if include_incoming:
                edges += self.get_incoming(current_id, dependency_types)

            for edge in edges:
                if edge.confidence < min_confidence or not edge.target_id:
                    continue

                next_id = edge.target_id if edge.source_id == current_id else edge.source_id
                if next_id in expanded:
                    continue

                expanded.add(next_id)
                queue.append((next_id, depth + 1))

        return expanded
