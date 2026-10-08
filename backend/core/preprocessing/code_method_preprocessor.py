from core.schemas import Artifact, Element, ElementLevel
from core.preprocessing.base import Preprocessor
from core.parser import BaseParser, JavaParser, PythonParser, JsTsParser

EXTENSION_TO_PARSER = {
    '.py': lambda: PythonParser(),
    '.java': lambda: JavaParser(),
    '.js': lambda: JsTsParser(is_typescript=False),
    '.ts': lambda: JsTsParser(is_typescript=True)
}

class CodeMethodPreprocessor(Preprocessor):

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            extension = self._get_extension(artifact.identifier)
            parser_factory = EXTENSION_TO_PARSER.get(extension)
            if parser_factory is None:
                continue  # Skip unsupported file types
            parser = parser_factory()
            elements += self._process_artifact(artifact, parser)
        return elements

    def _process_artifact(self, artifact: Artifact, parser: BaseParser) -> list[Element]:
        elements = []
        content = artifact.content.encode('utf-8')
        tree = parser.parse(content)

        file_element = Element(
            identifier=artifact.identifier,
            type=artifact.type,
            content=artifact.content,
            granularity=0,
            level=ElementLevel.FILE,
            parent_id=None,
            compare=False
        )
        elements.append(file_element)

        # A file can hold two functions with one name and one parameter list -
        # in different scopes, or in minified code. The first keeps the name;
        # each later one is numbered, because an identifier names one element.
        seen: dict[str, int] = {}

        def unique(identifier: str) -> str:
            seen[identifier] = seen.get(identifier, 0) + 1
            return identifier if seen[identifier] == 1 else f"{identifier}#{seen[identifier]}"

        classes = parser.find_classes(tree.root_node)

        if classes:
            for class_node in classes:
                class_name = parser.extract_name(class_node)
                class_content = content[class_node.start_byte:class_node.end_byte].decode('utf-8')
                class_id = unique(f"{artifact.identifier}::{class_name}")
                class_semantic_units = parser.extract_semantic_units(class_node, class_node=class_node)

                class_element = Element(
                    identifier=class_id,
                    type=f"source code class definition",
                    content=class_content,
                    granularity=1,
                    level=ElementLevel.CLASS,
                    parent_id=artifact.identifier,
                    compare=False,
                    semantic_units=class_semantic_units
                )
                elements.append(class_element)

                for method_name, method_node in parser.find_class_methods(class_node):
                    method_params = parser.extract_params(method_node)
                    method_content = content[method_node.start_byte:method_node.end_byte].decode('utf-8')
                    method_semantic_units = parser.extract_semantic_units(method_node, class_node=class_node)

                    method_element = Element(
                        identifier=unique(f"{class_id}::{method_name}{method_params}"),
                        type=f"source code method",
                        content=method_content,
                        granularity=2,
                        level=ElementLevel.FUNCTION,
                        parent_id=class_id,
                        compare=True,
                        semantic_units=method_semantic_units
                    )
                    elements.append(method_element)

        for function_name, function_node in parser.find_top_level_functions(tree.root_node):
            function_params = parser.extract_params(function_node)
            function_content = content[function_node.start_byte:function_node.end_byte].decode('utf-8')
            function_semantic_units = parser.extract_semantic_units(function_node, class_node=None)

            function_element = Element(
                identifier=unique(f"{artifact.identifier}::{function_name}{function_params}"),
                type='source code method',
                content=function_content,
                granularity=1,
                level=ElementLevel.FUNCTION,
                parent_id=artifact.identifier,
                compare=True,
                semantic_units=function_semantic_units
            )
            elements.append(function_element)

        return elements

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''