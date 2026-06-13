import os
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.java': 'java',
    '.js': 'javascript',
    '.ts': 'typescript'
}

class CodeTreePreprocessor(Preprocessor):

    def __init__(self, compare_classes: bool = False):
        self.compare_classes = compare_classes

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        language = self._detect_language(artifacts)
        packages = self._group_by_package(artifacts, language)
        return self._build_elements(packages)

    def _detect_language(self, artifacts: list[Artifact]) -> str:
        for artifact in artifacts:
            extension = self._get_extension(artifact.identifier)
            language = EXTENSION_TO_LANGUAGE.get(extension)
            if language:
                return language
        return 'unknown'

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''

    def _group_by_package(self, artifacts: list[Artifact], language: str) -> dict[str, list[Artifact]]:
        packages: dict[str, list[Artifact]] = {}
        for artifact in artifacts:
            package_name = self._get_package_name(artifact, language)
            packages.setdefault(package_name, []).append(artifact)
        return packages

    def _get_package_name(self, artifact: Artifact, language: str) -> str:
        match language:
            case 'java':
                return self._get_java_package(artifact)
            case _:
                return self._get_folder_package(artifact)

    def _get_java_package(self, artifact: Artifact) -> str:
        for line in artifact.content.splitlines():
            if line.strip().startswith('package '):
                return line.strip().split(' ')[1].replace(';', '')
        return self._get_folder_package(artifact)

    def _get_folder_package(self, artifact: Artifact) -> str:
        return os.path.dirname(artifact.identifier)

    def _build_elements(self, packages: dict[str, list[Artifact]]) -> list[Element]:
        elements = []
        for package_name, artifacts in packages.items():
            class_names = [os.path.basename(a.identifier) for a in artifacts]
            package_description = (
                f"This package is called {package_name} and contains the following classes: {', '.join(class_names)}."
            )
            package_element = Element(
                identifier=f"package-{package_name}",
                type="source code package definition",
                content=package_description,
                granularity=0,
                parent_id=None,
                compare=True
            )
            elements.append(package_element)

            for artifact in artifacts:
                class_element = Element(
                    identifier=artifact.identifier,
                    type="source code class definition",
                    content=artifact.content,
                    granularity=1,
                    parent_id=f"package-{package_name}",
                    compare=self.compare_classes
                )
                elements.append(class_element)
        return elements