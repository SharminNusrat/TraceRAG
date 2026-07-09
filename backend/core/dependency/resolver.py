import os
import re
from collections import defaultdict
from core.dependency.base import CodeDependency
from core.schemas import Element

class DependencyResolver:
    def __init__(self, elements: list[Element]):
        self.elements = elements
        self.by_id = {element.identifier: element for element in elements}
        self.by_simple_name: dict[str, list[str]] = defaultdict(list)
        self.by_file_basename: dict[str, list[str]] = defaultdict(list)
        self.by_file_path: dict[str, list[str]] = defaultdict(list)

        for element in elements:
            simple_name = self._simple_element_name(element.identifier)
            if simple_name:
                self.by_simple_name[simple_name].append(element.identifier)

            file_id = element.identifier.split("::")[0]
            basename = os.path.splitext(os.path.basename(file_id))[0]
            if basename:
                self.by_file_basename[basename].append(element.identifier)
            self.by_file_path[self._normalize_path(file_id)].append(element.identifier)

    def resolve(self, dependency: CodeDependency) -> CodeDependency:
        updates = {}
        if dependency.source_id not in self.by_id:
            source_id = self._resolve_source_id(dependency.source_id)
            if source_id is not None:
                updates["source_id"] = source_id

        if dependency.target_id or not dependency.target_name:
            return self._copy_dependency(dependency, updates) if updates else dependency

        source_id_for_resolution = updates.get("source_id", dependency.source_id)
        target_id = self._resolve_target_name(dependency.target_name, source_id_for_resolution)
        if target_id is not None:
            updates["target_id"] = target_id

        return self._copy_dependency(dependency, updates) if updates else dependency

    def resolve_all(self, dependencies: list[CodeDependency]) -> list[CodeDependency]:
        return [self.resolve(dependency) for dependency in dependencies]

    def _resolve_target_name(self, target_name: str, source_id: str) -> str | None:
        candidates = self._candidate_names(target_name)
        for candidate in candidates:
            ids = self.by_simple_name.get(candidate, [])
            if ids:
                return self._prefer_same_file(ids, source_id)

            ids = self.by_file_basename.get(candidate, [])
            if ids:
                return self._prefer_same_file(ids, source_id)

        return None

    def _resolve_source_id(self, source_id: str) -> str | None:
        source_file = source_id.split("::")[0]
        source_name = self._simple_element_name(source_id)
        candidates = [
            element_id
            for element_id in self.by_simple_name.get(source_name, [])
            if self._same_file(element_id.split("::")[0], source_file)
        ]
        if candidates:
            return candidates[0]

        file_candidates = self.by_file_path.get(self._normalize_path(source_file), [])
        if file_candidates:
            return file_candidates[0]

        return self.by_id.get(source_file).identifier if source_file in self.by_id else None

    def _candidate_names(self, target_name: str) -> list[str]:
        normalized = target_name.strip().strip(";")
        normalized = re.sub(r"['\"]", "", normalized)
        names = [normalized]

        for separator in (".", "/", "\\"):
            if separator in normalized:
                names.append(normalized.split(separator)[-1])

        if "(" in normalized:
            names.append(normalized.split("(", 1)[0])

        expanded = []
        for name in names:
            expanded.append(name)
            expanded.append(os.path.splitext(os.path.basename(name))[0])

        return [name for name in dict.fromkeys(expanded) if name]

    def _prefer_same_file(self, candidates: list[str], source_id: str) -> str:
        source_file = source_id.split("::")[0]
        for candidate in candidates:
            if self._same_file(candidate.split("::")[0], source_file):
                return candidate
        return candidates[0]

    def _simple_element_name(self, identifier: str) -> str:
        tail = identifier.split("::")[-1]
        tail = tail.split("(", 1)[0]
        tail = os.path.splitext(os.path.basename(tail))[0]
        return tail

    def _copy_dependency(self, dependency: CodeDependency, updates: dict) -> CodeDependency:
        if hasattr(dependency, "model_copy"):
            return dependency.model_copy(update=updates)
        return dependency.copy(update=updates)

    def _normalize_path(self, path: str) -> str:
        return os.path.abspath(path).replace("\\", "/").lower()

    def _same_file(self, left: str, right: str) -> bool:
        return self._normalize_path(left) == self._normalize_path(right)
