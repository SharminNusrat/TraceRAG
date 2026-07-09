from abc import ABC, abstractmethod
from core.schemas import CodeSemanticUnits
from tree_sitter import Language, Node

class BaseParser(ABC):

    @abstractmethod
    def get_language(self) -> Language:
        pass

    @abstractmethod
    def get_class_node_types(self) -> list[str]:
        pass

    @abstractmethod
    def get_method_node_types(self) -> list[str]:
        pass

    @abstractmethod
    def extract_name(self, node: Node) -> str:
        pass

    @abstractmethod
    def extract_params(self, node: Node) -> str:
        pass

    @abstractmethod
    def extract_semantic_units(self, node: Node, class_node: Node | None = None) -> CodeSemanticUnits:
        pass

    def find_nodes(self, node: Node, node_type: str) -> list[Node]:
        if node.type == node_type:
            return [node]
        nodes = []
        for child in node.children:
            nodes += self.find_nodes(child, node_type)
        return nodes

    def find_classes(self, root: Node) -> list[Node]:
        classes = []
        for node_type in self.get_class_node_types():
            classes += self.find_nodes(root, node_type)
        return classes

    def find_class_methods(self, class_node: Node) -> list[tuple[str, Node]]:
        methods = []
        for node_type in self.get_method_node_types():
            for method_node in self.find_nodes(class_node, node_type):
                method_name = self.extract_name(method_node)
                methods.append((method_name, method_node))
        return methods

    def find_top_level_functions(self, root: Node) -> list[tuple[str, Node]]:
        functions = []
        class_node_types = self.get_class_node_types()
        for node_type in self.get_method_node_types():
            for child in root.children:
                if child.type in class_node_types:
                    continue  # Skip class nodes
                if child.type == node_type:
                    function_name = self.extract_name(child)
                    functions.append((function_name, child))
        return functions

    def parse(self, content: bytes):
        from tree_sitter import Parser
        parser = Parser(self.get_language())
        return parser.parse(content)