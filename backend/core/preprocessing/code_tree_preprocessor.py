import os
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor
from core.parser import BaseParser, JavaParser, PythonParser, JsTsParser

EXTENSION_TO_PARSER = {
    '.py': lambda: PythonParser(),
    '.java': lambda: JavaParser(),
    '.js': lambda: JsTsParser(is_typescript=False),
    '.ts': lambda: JsTsParser(is_typescript=True),
    '.tsx': lambda: JsTsParser(is_typescript=True),
}

class CodeTreePreprocessor(Preprocessor):

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        processed_folders = set()
        common_root = self._get_common_root(artifacts)

        for artifact in artifacts:
            parts = []
            current_dir = os.path.dirname(os.path.abspath(artifact.identifier))

            while current_dir and current_dir != os.path.dirname(current_dir):
                parts.append(current_dir)
                current_dir = os.path.dirname(current_dir)

            for folder in reversed(parts):
                normalized_folder = folder.replace('\\', '/')
                if normalized_folder not in processed_folders:
                    p_dir = os.path.dirname(folder).replace('\\', '/')
                    parent_id = p_dir if p_dir != normalized_folder else None
                    depth = self._get_relative_depth(folder, common_root)

                    folder_element = Element(
                        identifier=normalized_folder,
                        type='source code package',
                        content=f"Package folder: '{os.path.basename(folder)}'",
                        granularity=depth,
                        parent_id=parent_id,
                        compare=False
                    )
                    elements.append(folder_element)
                    processed_folders.add(normalized_folder)

        for artifact in artifacts:
            abs_path = os.path.abspath(artifact.identifier).replace('\\', '/')
            parent_dir = os.path.dirname(abs_path)
            parent_id = parent_dir if parent_dir != abs_path else None
            file_granularity = self._get_relative_depth(abs_path, common_root)
            elements += self._process_artifact(artifact, base_granularity=file_granularity, parent_id=parent_id)

        return elements

    def _process_artifact(self, artifact: Artifact, base_granularity: int, parent_id: str | None) -> list[Element]:
        elements = []
        abs_id = os.path.abspath(artifact.identifier).replace('\\', '/')
        extension = self._get_extension(abs_id)
        parser_factory = EXTENSION_TO_PARSER.get(extension)

        file_element = Element(
            identifier=abs_id,
            type=artifact.type,
            content=artifact.content,
            granularity=base_granularity,
            parent_id=parent_id,
            compare=False
        )
        elements.append(file_element)

        if parser_factory is None:
            return elements

        parser = parser_factory()
        content = artifact.content.encode('utf-8')
        tree = parser.parse(content)

        seen_classes: dict[str, int] = {}
        for class_node in parser.find_classes(tree.root_node):
            class_name = parser.extract_name(class_node)
            count = seen_classes.get(class_name, 0)
            seen_classes[class_name] = count + 1
            class_id = f"{abs_id}::{class_name}" if count == 0 else f"{abs_id}::{class_name}_{count}"

            class_content = content[class_node.start_byte:class_node.end_byte].decode('utf-8')
            class_element = Element(
                identifier=class_id,
                type='source code class definition',
                content=class_content,
                granularity=base_granularity + 1,
                parent_id=abs_id,
                compare=False
            )
            elements.append(class_element)

            seen_methods: dict[str, int] = {}
            for method_name, method_node in parser.find_class_methods(class_node):
                method_count = seen_methods.get(method_name, 0)
                seen_methods[method_name] = method_count + 1
                method_id = f"{class_id}::{method_name}" if method_count == 0 else f"{class_id}::{method_name}_{method_count}"

                # method_params = parser.extract_params(method_node)
                method_content = content[method_node.start_byte:method_node.end_byte].decode('utf-8')
                method_element = Element(
                    identifier=method_id,
                    type='source code method',
                    content=method_content,
                    granularity=base_granularity + 2,
                    parent_id=class_id,
                    compare=False
                )
                elements.append(method_element)

        seen_functions: dict[str, int] = {}
        for function_name, function_node in parser.find_top_level_functions(tree.root_node):
            function_count = seen_functions.get(function_name, 0)
            seen_functions[function_name] = function_count + 1
            function_id = f"{abs_id}::{function_name}" if function_count == 0 else f"{abs_id}::{function_name}_{function_count}"
            function_content = content[function_node.start_byte:function_node.end_byte].decode('utf-8')
            function_element = Element(
                identifier=function_id,
                type='source code method',
                content=function_content,
                granularity=base_granularity + 1,
                parent_id=abs_id,
                compare=False
            )
            elements.append(function_element)

        return elements

    def _get_common_root(self, artifacts: list[Artifact]) -> str:
        if not artifacts:
            return ''
        paths = [os.path.dirname(os.path.abspath(a.identifier)) for a in artifacts]
        try:
            common = os.path.commonpath(paths).replace('\\', '/')
        except ValueError:
            common = ''
        return common

    def _get_relative_depth(self, path: str, common_root: str) -> int:
        abs_path = os.path.abspath(path).replace('\\', '/')
        if not common_root or not abs_path.startswith(common_root):
            return 0
        rel = abs_path[len(common_root):].strip('/')
        if not rel:
            return 0
        return len(rel.split('/'))

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''