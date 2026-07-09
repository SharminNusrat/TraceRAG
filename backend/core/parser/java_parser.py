import tree_sitter_java as tsjava
from tree_sitter import Language, Node
from core.parser.base import BaseParser
from core.schemas import CodeSemanticUnits


class JavaParser(BaseParser):

    def get_language(self) -> Language:
        return Language(tsjava.language())

    def get_class_node_types(self) -> list[str]:
        return ['class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration', 'annotation_type_declaration']

    def get_method_node_types(self) -> list[str]:
        return ['method_declaration', 'constructor_declaration', 'compact_constructor_declaration', 'annotation_type_element_declaration']

    def extract_name(self, node: Node) -> str:
        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')

        for child in node.children:
            if child.type == 'identifier':
                return child.text.decode('utf-8')
        return 'unknown'

    def extract_params(self, node: Node) -> str:
        if node.type == 'compact_constructor_declaration':
            return '()'

        for child in node.children:
            if child.type == 'formal_parameters':
                raw_text = child.text.decode('utf-8')
                normalized_text = ' '.join(raw_text.split())
                return normalized_text
        return '()'

    def extract_semantic_units(self, node: Node, class_node: Node | None = None) -> CodeSemanticUnits:
        units = CodeSemanticUnits()

        if class_node is not None:
            units.class_name = self.extract_name(class_node)
            units.class_comment = self._get_preceding_comment(class_node)
            units.class_attributes = self._extract_field_names(class_node)

        if node.type in self.get_method_node_types():
            units.method_name = self.extract_name(node)
            units.method_comment = self._get_preceding_comment(node)
            units.method_params = self._extract_param_names(node)
            units.return_type = self._extract_return_type(node)

        return units

    def _get_preceding_comment(self, node: Node) -> str | None:
        """Walks backwards to find comments, safely skipping annotations and modifiers."""
        prev = node.prev_sibling
        comments = []
        
        # Types of nodes we are allowed to skip over while looking for a higher comment block
        skippable_types = {"modifiers", "annotation", "marker_annotation", "modifiers_list"}
        
        while prev is not None:
            if prev.type in {"block_comment", "line_comment"}:
                comments.insert(0, prev.text.decode("utf-8"))
            elif prev.type not in skippable_types:
                # Break only when we hit an unrelated code construct
                break
            prev = prev.prev_sibling
            
        return "\n".join(comments) if comments else None

    def _extract_field_names(self, class_node: Node) -> list[str]:
        fields = []
        
        # Modern Edge Case 1: Record Parameters acting as fields: `record Point(int x) {}`
        if class_node.type == 'record_declaration':
            header = next((c for c in class_node.children if c.type == "record_header"), None)
            if header:
                for param in header.children:
                    if param.type == "formal_parameter":
                        name_node = param.child_by_field_name("name") or next((c for c in param.children if c.type == "identifier"), None)
                        if name_node:
                            fields.append(name_node.text.decode("utf-8"))
            return fields

        # Modern Edge Case 2: Enum Constants acting as values
        body_type = "enum_body" if class_node.type == "enum_declaration" else "class_body"
        body = next((c for c in class_node.children if c.type == body_type), None)
        if not body:
            return fields
            
        for child in body.children:
            if child.type == "field_declaration":
                for c in child.children:
                    if c.type == "variable_declarator":
                        # Anchor check fallback to naked identifier tokens
                        name_node = c.child_by_field_name("name") or next((sub for sub in c.children if sub.type == "identifier"), None)
                        if name_node:
                            fields.append(name_node.text.decode("utf-8"))
            elif child.type == "enum_constant":
                name_node = child.child_by_field_name("name") or next((sub for sub in child.children if sub.type == "identifier"), None)
                if name_node:
                    fields.append(name_node.text.decode("utf-8"))
                    
        return fields

    def _extract_param_names(self, node: Node) -> list[str]:
        if node.type == 'compact_constructor_declaration':
            return []
        for child in node.children:
            if child.type == 'formal_parameters':
                names = []
                for param in child.children:
                    # Handles normal parameters and vararg (spread_parameter) patterns safely
                    if param.type in {'formal_parameter', 'spread_parameter'}:
                        name_node = param.child_by_field_name('name') or next((c for c in param.children if c.type == 'identifier'), None)
                        if name_node:
                            names.append(name_node.text.decode('utf-8'))
                return names
        return []

    def _extract_return_type(self, node: Node) -> str | None:
        type_node = node.child_by_field_name("type")
        if type_node:
            return type_node.text.decode("utf-8")
        return None
