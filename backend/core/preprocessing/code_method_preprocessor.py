import re
from tree_sitter import Language, Parser, Node
import tree_sitter_python as tspython
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.java': 'java',
    '.js': 'javascript',
    '.ts': 'typescript'
}

CLASS_NODES = {
    'python': 'class_definition',
    'java': 'class_declaration',
    'javascript': 'class_declaration',
    'typescript': 'class_declaration'
}

METHOD_NODES = {
    'python': ['function_definition'],
    'java': ['method_declaration', 'constructor_declaration'],
    'javascript': ['method_definition', 'function_declaration', 'arrow_function'],
    'typescript': ['method_definition', 'function_declaration', 'arrow_function']
}

def _get_ts_language(language: str) -> Language:
    match language:
        case 'python':
            return Language(tspython.language())
        case 'java':
            return Language(tsjava.language())
        case 'javascript':
            return Language(tsjavascript.language())
        case 'typescript':
            return Language(tstypescript.language_typescript())

class CodeMethodPreprocessor(Preprocessor):

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            extension = self._get_extension(artifact.identifier)
            language = EXTENSION_TO_LANGUAGE.get(extension)
            if language is None:
                continue  # Skip unsupported file types
            elements += self._process_artifact(artifact, language)
        return elements

    def _process_artifact(self, artifact: Artifact, language: str) -> list[Element]:
        elements = []
        content = artifact.content.encode('utf-8')
        parser = Parser(_get_ts_language(language))
        tree = parser.parse(content)

        file_element = Element(
            identifier=artifact.identifier,
            type=artifact.type,
            content=artifact.content,
            granularity=0,
            parent_id=None,
            compare=False
        )
        elements.append(file_element)

        class_node_type = CLASS_NODES[language]
        method_node_types = METHOD_NODES[language]

        classes = self._find_nodes(tree.root_node, class_node_type)

        if classes:
            for class_node in classes:
                class_name = self._extract_name(class_node, language)
                class_content = self._strip_comments(content[class_node.start_byte:class_node.end_byte].decode('utf-8'), language)
                class_id = f"{artifact.identifier}::{class_name}"
                class_element = Element(
                    identifier=class_id,
                    type=f"source code class definition",
                    content=class_content,
                    granularity=1,
                    parent_id=artifact.identifier,
                    compare=False
                )
                elements.append(class_element)

                for node_type in method_node_types:
                    if node_type == "arrow_function":
                        continue
                    methods = self._find_nodes(class_node, node_type)
                    for method_node in methods:
                        method_name = self._extract_name(method_node, language)
                        method_params = self._extract_params(method_node, language)
                        method_content = self._strip_comments(content[method_node.start_byte:method_node.end_byte].decode('utf-8'), language)
                        method_element = Element(
                            identifier=f"{class_id}::{method_name}{method_params}",
                            type=f"source code method",
                            content=method_content,
                            granularity=2,
                            parent_id=class_id,
                            compare=True
                        )
                        elements.append(method_element)

        for node_type in method_node_types:
            if node_type == "arrow_function":
                continue
            top_level = self._find_top_level_nodes(tree.root_node, class_node_type, node_type)
            for func_node in top_level:
                func_name = self._extract_name(func_node, language)
                func_params = self._extract_params(func_node, language)
                func_content = self._strip_comments(content[func_node.start_byte:func_node.end_byte].decode('utf-8'), language)
                func_element = Element(
                    identifier=f"{artifact.identifier}::{func_name}{func_params}",
                    type=f"source code method",
                    content=func_content,
                    granularity=1,
                    parent_id=artifact.identifier,
                    compare=True
                )
                elements.append(func_element)

        if language in ("javascript", "typescript"):
            arrow_functions = self._find_arrow_functions(tree.root_node, class_node_type)
            for func_name, arrow_node in arrow_functions:
                func_params = self._extract_params(arrow_node, language)
                func_content = self._strip_comments(
                    content[arrow_node.start_byte:arrow_node.end_byte].decode('utf-8'),
                    language
                )
                elements.append(Element(
                    identifier=f"{artifact.identifier}::{func_name}{func_params}",
                    type='source code method',
                    content=func_content,
                    granularity=1,
                    parent_id=artifact.identifier,
                    compare=True
                ))
        return elements

    def _find_arrow_functions(self, root: Node, class_node_type: str) -> list[tuple[str, Node]]:
        """Returns list of (name, arrow_function_node) tuples"""
        results = []
        
        def extract_from_declarator(decl_node):
            name = None
            arrow_node = None
            for child in decl_node.children:
                if child.type == "identifier":
                    name = child.text.decode("utf-8")
                elif child.type == "arrow_function":
                    arrow_node = child
            if name and arrow_node:
                results.append((name, arrow_node))

        for child in root.children:
            if child.type == class_node_type:
                continue
            if child.type in {"lexical_declaration", "variable_declaration"}:
                for decl in child.children:
                    if decl.type == "variable_declarator":
                        extract_from_declarator(decl)
            elif child.type == "export_statement":
                for export_child in child.children:
                    if export_child.type in {"lexical_declaration", "variable_declaration"}:
                        for decl in export_child.children:
                            if decl.type == "variable_declarator":
                                extract_from_declarator(decl)
        return results

    def _strip_comments(self, content: str, language: str) -> str:
        match language:
            case "java" | "javascript" | "typescript":
                content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
            case "python":
                content = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
                content = re.sub(r"'''.*?'''", '', content, flags=re.DOTALL)
                content = re.sub(r'#.*?$', '', content, flags=re.MULTILINE)
        return content.strip()

    def _extract_name(self, node: Node, language: str) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return name_node.text.decode("utf-8")

        name_types = {"identifier", "property_identifier", "type_identifier"}
        for child in node.children:
            if child.type in name_types:
                return child.text.decode("utf-8")
        return "unknown"

    def _extract_params(self, node: Node, language: str) -> str:
        match language:
            case 'python':
                return self._extract_python_params(node)
            case 'java':
                return self._extract_java_params(node)
            case 'javascript' | 'typescript':
                return self._extract_js_ts_params(node)
            case _:
                return "()"

    def _extract_python_params(self, node: Node) -> str:
        for child in node.children:
            if child.type == "parameters":
                params = []
                for param in child.children:
                    if param.type == "identifier" and param.text.decode('utf-8') != "self":
                        params.append(param.text.decode('utf-8'))
                    elif param.type in {"typed_parameter", "default_parameter", "typed_default_parameter"}:
                        name = next((c.text.decode("utf-8") for c in param.children if c.type == "identifier"), None)
                        if name and name != "self":
                            params.append(name)
                return f"({', '.join(params)})"
        return "()"

    def _extract_java_params(self, node: Node) -> str:
        for child in node.children:
            if child.type == "formal_parameters":
                return child.text.decode("utf-8")
        return "()"

    def _extract_js_ts_params(self, node: Node) -> str: 
        for child in node.children:
            if child.type == "formal_parameters":
                params = []
                for param in child.children:
                    if param.type == "identifier":
                        params.append(param.text.decode("utf-8"))
                    elif param.type in {"assignment_pattern", "rest_pattern"}:
                        name = next((c.text.decode("utf-8") for c in param.children if c.type == "identifier"), None)
                        if name:
                            params.append(name)
                return f"({', '.join(params)})"
        return "()"

    def _find_nodes(self, node: Node, node_type: str) -> list[Node]:
        if node.type == node_type:
            return [node]
        nodes = []
        for child in node.children:
            nodes += self._find_nodes(child, node_type)
        return nodes

    def _find_top_level_nodes(self, root: Node, class_node_type: str, target_type: str) -> list[Node]:
        nodes = []
        for child in root.children:
            if child.type == class_node_type:
                continue  # Skip class nodes because methods inside classes were already processed.
            if child.type == target_type:
                nodes.append(child)
            if child.type == "export_statement":
                for export_child in child.children:
                    if export_child.type == target_type:
                        nodes.append(export_child)
                    elif export_child.type in {"lexical_declaration", "variable_declaration"}:
                        for decl in export_child.children:
                            if decl.type == "variable_declarator":
                                for var_child in decl.children:
                                    if var_child.type == target_type:
                                        nodes.append(var_child)

            if child.type in {"lexical_declaration", "variable_declaration"}:
                for decl in child.children:
                    if decl.type == "variable_declarator":
                        for var_child in decl.children:
                            if var_child.type == target_type:
                                nodes.append(var_child)
        return nodes

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''