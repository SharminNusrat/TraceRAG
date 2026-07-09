from abc import ABC, abstractmethod
import os
import re
from tree_sitter import Node
from core.dependency.base import CodeDependency, DependencyType
from core.ingestion.base import Artifact
from core.parser import BaseParser, JavaParser, JsTsParser, PythonParser


class LanguageDependencyAnalyzer(ABC):
    def __init__(self, parser: BaseParser):
        self.parser = parser

    def analyze(self, artifact: Artifact) -> list[CodeDependency]:
        content = artifact.content.encode("utf-8")
        tree = self.parser.parse(content)
        return self._analyze_tree(artifact.identifier, content, tree.root_node)

    @abstractmethod
    def _analyze_tree(self, artifact_id: str, content: bytes, root: Node) -> list[CodeDependency]:
        pass

    def _edge(
        self,
        source_id: str,
        dependency_type: DependencyType,
        target_name: str | None,
        evidence: str | None = None,
        confidence: float = 1.0
    ) -> CodeDependency | None:
        if not target_name:
            return None
        clean_target = self._clean_name(target_name)
        if not clean_target:
            return None
        return CodeDependency(
            source_id=source_id,
            dependency_type=dependency_type,
            target_name=clean_target,
            confidence=confidence,
            evidence=evidence
        )

    def _walk(self, node: Node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _text(self, node: Node | None) -> str | None:
        if node is None:
            return None
        return node.text.decode("utf-8", errors="ignore")

    def _clean_name(self, value: str) -> str:
        value = value.strip()
        value = re.sub(r"^(import|from|package|extends|implements|new)\s+", "", value)
        value = value.rstrip(";")
        value = value.strip("(){}[] \n\t")
        return value

    def _method_id(self, artifact_id: str, class_name: str | None, method_node: Node) -> str:
        method_name = self.parser.extract_name(method_node)
        params = self.parser.extract_params(method_node)
        if class_name:
            return f"{artifact_id}::{class_name}::{method_name}{params}"
        return f"{artifact_id}::{method_name}{params}"

    def _class_id(self, artifact_id: str, class_name: str) -> str:
        return f"{artifact_id}::{class_name}"


class JavaDependencyAnalyzer(LanguageDependencyAnalyzer):
    def __init__(self):
        super().__init__(JavaParser())

    def _analyze_tree(self, artifact_id: str, content: bytes, root: Node) -> list[CodeDependency]:
        edges: list[CodeDependency] = []

        for node in self._walk(root):
            if node.type == "package_declaration":
                edge = self._edge(artifact_id, DependencyType.PACKAGE_MEMBER, self._text(node), self._text(node))
                if edge:
                    edges.append(edge)

            elif node.type == "import_declaration":
                edge = self._edge(artifact_id, DependencyType.IMPORT, self._java_import_name(node), self._text(node))
                if edge:
                    edges.append(edge)

        for class_node in self.parser.find_classes(root):
            class_name = self.parser.extract_name(class_node)
            class_id = self._class_id(artifact_id, class_name)
            edges += self._java_class_edges(class_id, class_node)

            for method_name, method_node in self.parser.find_class_methods(class_node):
                method_id = self._method_id(artifact_id, class_name, method_node)
                edges.append(CodeDependency(
                    source_id=method_id,
                    target_id=class_id,
                    dependency_type=DependencyType.SAME_CLASS,
                    confidence=1.0,
                    evidence=method_name
                ))
                edges += self._java_method_edges(method_id, method_node)

        return edges

    def _java_import_name(self, node: Node) -> str | None:
        text = self._text(node)
        if text is None:
            return None
        return text.replace("import", "").replace("static", "").replace(";", "").strip()

    def _java_class_edges(self, class_id: str, class_node: Node) -> list[CodeDependency]:
        edges = []
        superclass = class_node.child_by_field_name("superclass")
        if superclass:
            edge = self._edge(class_id, DependencyType.EXTENDS, self._text(superclass), self._text(superclass))
            if edge:
                edges.append(edge)

        interfaces = class_node.child_by_field_name("interfaces")
        if interfaces:
            for identifier in self._identifiers(interfaces):
                edge = self._edge(class_id, DependencyType.IMPLEMENTS, identifier, self._text(interfaces))
                if edge:
                    edges.append(edge)

        for child in class_node.children:
            if child.type == "field_declaration":
                type_node = child.child_by_field_name("type")
                edge = self._edge(class_id, DependencyType.FIELD_TYPE, self._text(type_node), self._text(child), 0.8)
                if edge:
                    edges.append(edge)

        return edges

    def _java_method_edges(self, method_id: str, method_node: Node) -> list[CodeDependency]:
        edges = []
        return_type = method_node.child_by_field_name("type")
        edge = self._edge(method_id, DependencyType.RETURN_TYPE, self._text(return_type), self._text(return_type), 0.8)
        if edge:
            edges.append(edge)

        params = next((child for child in method_node.children if child.type == "formal_parameters"), None)
        if params:
            for param in params.children:
                if param.type in {"formal_parameter", "spread_parameter"}:
                    type_node = param.child_by_field_name("type")
                    edge = self._edge(method_id, DependencyType.PARAM_TYPE, self._text(type_node), self._text(param), 0.8)
                    if edge:
                        edges.append(edge)

        for node in self._walk(method_node):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                edge = self._edge(method_id, DependencyType.CALLS, self._text(name_node), self._text(node), 0.7)
                if edge:
                    edges.append(edge)
            elif node.type == "object_creation_expression":
                type_node = node.child_by_field_name("type")
                edge = self._edge(method_id, DependencyType.INSTANTIATES, self._text(type_node), self._text(node), 0.8)
                if edge:
                    edges.append(edge)

        return edges

    def _identifiers(self, node: Node) -> list[str]:
        identifiers = []
        for child in self._walk(node):
            if child.type in {"identifier", "type_identifier"}:
                text = self._text(child)
                if text:
                    identifiers.append(text)
        return identifiers


class PythonDependencyAnalyzer(LanguageDependencyAnalyzer):
    def __init__(self):
        super().__init__(PythonParser())

    def _analyze_tree(self, artifact_id: str, content: bytes, root: Node) -> list[CodeDependency]:
        edges = []

        for node in self._walk(root):
            if node.type in {"import_statement", "import_from_statement"}:
                for name in self._python_import_names(node):
                    edge = self._edge(artifact_id, DependencyType.IMPORT, name, self._text(node))
                    if edge:
                        edges.append(edge)

        for class_node in self.parser.find_classes(root):
            class_name = self.parser.extract_name(class_node)
            class_id = self._class_id(artifact_id, class_name)
            edges += self._python_class_edges(class_id, class_node)

            for method_name, method_node in self.parser.find_class_methods(class_node):
                method_id = self._method_id(artifact_id, class_name, method_node)
                edges.append(CodeDependency(
                    source_id=method_id,
                    target_id=class_id,
                    dependency_type=DependencyType.SAME_CLASS,
                    confidence=1.0,
                    evidence=method_name
                ))
                edges += self._python_callable_edges(method_id, method_node)

        for function_name, function_node in self.parser.find_top_level_functions(root):
            function_id = self._method_id(artifact_id, None, function_node)
            edges += self._python_callable_edges(function_id, function_node)

        return edges

    def _python_import_names(self, node: Node) -> list[str]:
        names = []
        for child in self._walk(node):
            if child.type in {"dotted_name", "identifier", "aliased_import"}:
                text = self._text(child)
                if text and text not in {"import", "from"}:
                    names.append(text.split(" as ")[0].strip())
        return list(dict.fromkeys(names))

    def _python_class_edges(self, class_id: str, class_node: Node) -> list[CodeDependency]:
        edges = []
        argument_list = next((child for child in class_node.children if child.type == "argument_list"), None)
        if argument_list:
            for identifier in self._python_call_names(argument_list):
                edge = self._edge(class_id, DependencyType.EXTENDS, identifier, self._text(argument_list), 0.9)
                if edge:
                    edges.append(edge)
        return edges

    def _python_callable_edges(self, source_id: str, function_node: Node) -> list[CodeDependency]:
        edges = []
        return_type = function_node.child_by_field_name("return_type")
        edge = self._edge(source_id, DependencyType.RETURN_TYPE, self._text(return_type), self._text(return_type), 0.8)
        if edge:
            edges.append(edge)

        parameters = next((child for child in function_node.children if child.type == "parameters"), None)
        if parameters:
            for param in parameters.children:
                type_node = param.child_by_field_name("type")
                edge = self._edge(source_id, DependencyType.PARAM_TYPE, self._text(type_node), self._text(param), 0.8)
                if edge:
                    edges.append(edge)

        for node in self._walk(function_node):
            if node.type == "call":
                function = node.child_by_field_name("function")
                target = self._text(function)
                edge = self._edge(source_id, DependencyType.CALLS, target, self._text(node), 0.7)
                if edge:
                    edges.append(edge)

        return edges

    def _python_call_names(self, node: Node) -> list[str]:
        names = []
        for child in self._walk(node):
            if child.type in {"identifier", "attribute"}:
                text = self._text(child)
                if text:
                    names.append(text.split(".")[-1])
        return names


class JsTsDependencyAnalyzer(LanguageDependencyAnalyzer):
    def __init__(self, is_typescript: bool = False):
        super().__init__(JsTsParser(is_typescript=is_typescript))

    def _analyze_tree(self, artifact_id: str, content: bytes, root: Node) -> list[CodeDependency]:
        edges = []

        for node in self._walk(root):
            if node.type == "import_statement":
                edge = self._edge(artifact_id, DependencyType.IMPORT, self._js_import_name(node), self._text(node))
                if edge:
                    edges.append(edge)

        for class_node in self.parser.find_classes(root):
            class_name = self.parser.extract_name(class_node)
            class_id = self._class_id(artifact_id, class_name)
            edges += self._js_class_edges(class_id, class_node)

            for method_name, method_node in self.parser.find_class_methods(class_node):
                method_id = self._method_id(artifact_id, class_name, method_node)
                edges.append(CodeDependency(
                    source_id=method_id,
                    target_id=class_id,
                    dependency_type=DependencyType.SAME_CLASS,
                    confidence=1.0,
                    evidence=method_name
                ))
                edges += self._js_callable_edges(method_id, method_node)

        for function_name, function_node in self.parser.find_top_level_functions(root):
            function_id = self._method_id(artifact_id, None, function_node)
            edges += self._js_callable_edges(function_id, function_node)

        return edges

    def _js_import_name(self, node: Node) -> str | None:
        text = self._text(node)
        if text is None:
            return None
        match = re.search(r"from\s+['\"]([^'\"]+)['\"]", text)
        if match:
            return match.group(1)
        match = re.search(r"import\s+['\"]([^'\"]+)['\"]", text)
        if match:
            return match.group(1)
        return text

    def _js_class_edges(self, class_id: str, class_node: Node) -> list[CodeDependency]:
        edges = []
        for child in class_node.children:
            if child.type == "class_heritage":
                for identifier in self._js_identifiers(child):
                    edge = self._edge(class_id, DependencyType.EXTENDS, identifier, self._text(child), 0.9)
                    if edge:
                        edges.append(edge)
        return edges

    def _js_callable_edges(self, source_id: str, function_node: Node) -> list[CodeDependency]:
        edges = []
        return_type = function_node.child_by_field_name("return_type")
        edge = self._edge(source_id, DependencyType.RETURN_TYPE, self._text(return_type), self._text(return_type), 0.8)
        if edge:
            edges.append(edge)

        for node in self._walk(function_node):
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                edge = self._edge(source_id, DependencyType.CALLS, self._text(function), self._text(node), 0.7)
                if edge:
                    edges.append(edge)
            elif node.type == "new_expression":
                constructor = node.child_by_field_name("constructor")
                edge = self._edge(source_id, DependencyType.INSTANTIATES, self._text(constructor), self._text(node), 0.8)
                if edge:
                    edges.append(edge)

        return edges

    def _js_identifiers(self, node: Node) -> list[str]:
        names = []
        for child in self._walk(node):
            if child.type in {"identifier", "type_identifier", "property_identifier"}:
                text = self._text(child)
                if text:
                    names.append(text)
        return names


EXTENSION_TO_ANALYZER = {
    ".java": lambda: JavaDependencyAnalyzer(),
    ".py": lambda: PythonDependencyAnalyzer(),
    ".js": lambda: JsTsDependencyAnalyzer(is_typescript=False),
    ".ts": lambda: JsTsDependencyAnalyzer(is_typescript=True),
    ".tsx": lambda: JsTsDependencyAnalyzer(is_typescript=True),
}

def get_dependency_analyzer_for_path(path: str) -> LanguageDependencyAnalyzer | None:
    extension = os.path.splitext(path)[1].lower()
    factory = EXTENSION_TO_ANALYZER.get(extension)
    return factory() if factory else None