import os
import re
from collections import defaultdict
from core.dependency.base import CodeDependency, DependencyType
from core.schemas import Element

class DependencyResolver:
    def __init__(self, elements: list[Element]):
        self.elements = elements
        self.by_id = {element.identifier: element for element in elements}
        self.by_simple_name: dict[str, list[str]] = defaultdict(list)
        self.by_file_basename: dict[str, list[str]] = defaultdict(list)
        self.by_file_path: dict[str, list[str]] = defaultdict(list)
        self.imported_type_names_by_file: dict[str, set[str]] = defaultdict(set)

        for element in elements:
            simple_name = self._simple_element_name(element.identifier)
            if simple_name:
                self.by_simple_name[simple_name].append(element.identifier)

            file_id = element.identifier.split("::")[0]
            basename = os.path.splitext(os.path.basename(file_id))[0]
            if basename:
                self.by_file_basename[basename].append(element.identifier)
            self.by_file_path[self._normalize_path(file_id)].append(element.identifier)

    def register_imports(self, dependencies: list[CodeDependency]) -> None:
        """Record imported type names so qualified static calls can be resolved safely."""
        for dependency in dependencies:
            if dependency.dependency_type != DependencyType.IMPORT or not dependency.target_name:
                continue
            source_file = self._normalize_path(dependency.source_id.split("::")[0])
            self.imported_type_names_by_file[source_file].update(
                self._candidate_names(dependency.target_name)
            )

    def resolve(self, dependency: CodeDependency) -> CodeDependency:
        updates = {}
        if dependency.source_id not in self.by_id:
            source_id = self._resolve_source_id(dependency.source_id)
            if source_id is not None:
                updates["source_id"] = source_id

        if dependency.target_id or not dependency.target_name:
            return self._copy_dependency(dependency, updates) if updates else dependency

        source_id_for_resolution = updates.get("source_id", dependency.source_id)
        target_id = self._resolve_target_name(
            dependency.target_name,
            source_id_for_resolution,
            dependency.dependency_type,
        )
        if target_id is not None:
            updates["target_id"] = target_id

        return self._copy_dependency(dependency, updates) if updates else dependency

    def resolve_all(self, dependencies: list[CodeDependency]) -> list[CodeDependency]:
        return [self.resolve(dependency) for dependency in dependencies]

    def _resolve_target_name(
        self,
        target_name: str,
        source_id: str,
        dependency_type: DependencyType,
    ) -> str | None:
        candidates = self._candidate_names(target_name)
        for candidate in candidates:
            ids = self._filter_by_dependency_type(
                self.by_simple_name.get(candidate, []), dependency_type
            )
            ids = self._filter_call_candidates_by_scope(
                ids, target_name, source_id, dependency_type
            )
            resolved = self._resolve_unambiguous(ids, source_id)
            if resolved is not None:
                return resolved

            ids = self._filter_by_dependency_type(
                self.by_file_basename.get(candidate, []), dependency_type
            )
            resolved = self._resolve_unambiguous(ids, source_id)
            if resolved is not None:
                return resolved

        return None

    def _resolve_source_id(self, source_id: str) -> str | None:
        source_file = source_id.split("::")[0]
        source_name = self._simple_element_name(source_id)
        candidates = [
            element_id
            for element_id in self.by_simple_name.get(source_name, [])
            if self._same_file(element_id.split("::")[0], source_file)
        ]
        if len(candidates) == 1:
            return candidates[0]

        file_candidates = self.by_file_path.get(self._normalize_path(source_file), [])
        if len(file_candidates) == 1:
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

    def _resolve_unambiguous(self, candidates: list[str], source_id: str) -> str | None:
        """Resolve only when local scope or the project-wide symbol is unambiguous."""
        source_file = source_id.split("::")[0]
        local_candidates = [
            candidate
            for candidate in candidates
            if self._same_file(candidate.split("::")[0], source_file)
        ]
        if len(local_candidates) == 1:
            return local_candidates[0]

        # A repeated symbol name is not enough evidence for a cross-file edge.
        return candidates[0] if len(candidates) == 1 else None

    def _filter_by_dependency_type(
        self,
        candidates: list[str],
        dependency_type: DependencyType,
    ) -> list[str]:
        if dependency_type == DependencyType.CALLS:
            return [candidate for candidate in candidates if self._is_callable(candidate)]

        if dependency_type in {
            DependencyType.EXTENDS,
            DependencyType.IMPLEMENTS,
            DependencyType.INSTANTIATES,
            DependencyType.FIELD_TYPE,
            DependencyType.PARAM_TYPE,
            DependencyType.RETURN_TYPE,
            DependencyType.IMPORT,
        }:
            return [candidate for candidate in candidates if self._is_type(candidate)]

        return candidates

    def _filter_call_candidates_by_scope(
        self,
        candidates: list[str],
        target_name: str,
        source_id: str,
        dependency_type: DependencyType,
    ) -> list[str]:
        if dependency_type != DependencyType.CALLS or "." not in target_name:
            return candidates

        qualifier = target_name.rsplit(".", 1)[0].split(".")[-1].strip()
        source_file = self._normalize_path(source_id.split("::")[0])
        imported_types = self.imported_type_names_by_file[source_file]

        if qualifier in {"this", "self"}:
            return candidates
        if qualifier not in imported_types:
            # The receiver's type is unknown. Do not guess from the method name.
            return []
        return [
            candidate
            for candidate in candidates
            if self._parent_type_name(candidate) == qualifier
        ]

    def _is_callable(self, identifier: str) -> bool:
        element = self.by_id[identifier]
        return "method" in element.type.lower() or "function" in element.type.lower()

    def _is_type(self, identifier: str) -> bool:
        element = self.by_id[identifier]
        return "class" in element.type.lower() or "interface" in element.type.lower()

    def _parent_type_name(self, identifier: str) -> str | None:
        parts = identifier.split("::")
        if len(parts) < 3:
            return None
        return parts[-2].split("(", 1)[0]

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
