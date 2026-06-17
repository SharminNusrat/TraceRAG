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
    'javascript': ['method_definition', 'function_declaration'],
    'typescript': ['method_definition', 'function_declaration']
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
            for i, class_node in enumerate(classes):
                class_content = content[class_node.start_byte:class_node.end_byte].decode('utf-8')
                class_element = Element(
                    identifier=f"{artifact.identifier}::class_{i}",
                    type=f"source code class definition",
                    content=class_content,
                    granularity=1,
                    parent_id=artifact.identifier,
                    compare=False
                )
                elements.append(class_element)

                for node_type in method_node_types:
                    methods = self._find_nodes(class_node, node_type)
                    for j, method_node in enumerate(methods):
                        method_content = content[method_node.start_byte:method_node.end_byte].decode('utf-8')
                        method_element = Element(
                            identifier=f"{artifact.identifier}::class_{i}::method_{j}",
                            type=f"source code method",
                            content=method_content,
                            granularity=2,
                            parent_id=f"{artifact.identifier}::class_{i}",
                            compare=True
                        )
                        elements.append(method_element)

        for node_type in method_node_types:
            top_level = self._find_top_level_nodes(tree.root_node, class_node_type, node_type)
            for i, func_node in enumerate(top_level):
                func_content = content[func_node.start_byte:func_node.end_byte].decode('utf-8')
                func_element = Element(
                    identifier=f"{artifact.identifier}::function_{i}",
                    type=f"source code method",
                    content=func_content,
                    granularity=1,
                    parent_id=artifact.identifier,
                    compare=True
                )
                elements.append(func_element)
        return elements

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
        return nodes

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''