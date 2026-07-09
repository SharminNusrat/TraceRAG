import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node
from core.parser.base import BaseParser
from core.schemas import CodeSemanticUnits


class JsTsParser(BaseParser):

    def __init__(self, is_typescript: bool = False):
        self.is_typescript = is_typescript

    def get_language(self) -> Language:
        if self.is_typescript:
            return Language(tstypescript.language_typescript())
        return Language(tsjavascript.language())

    def get_class_node_types(self) -> list[str]:
        return ['class_declaration', 'abstract_class_declaration', 'interface_declaration', 'enum_declaration']

    def get_method_node_types(self) -> list[str]:
        return ['method_definition', 'function_declaration', 'arrow_function', 'generator_function']

    def extract_name(self, node: Node) -> str:
        if node.type == 'arrow_function':
            return 'unknown'

        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')

        property_node = node.child_by_field_name('property')
        if property_node:
            return property_node.text.decode('utf-8')

        name_types = {'identifier', 'property_identifier', 'shorthand_property_identifier'}
        for child in node.children:
            if child.type in name_types:
                return child.text.decode('utf-8')
        return 'unknown'

    def extract_params(self, node: Node) -> str:
        if node.type == 'arrow_function':
            for child in node.children:
                if child.type == 'identifier':
                    return f'({child.text.decode("utf-8")})'

        for child in node.children:
            if child.type in {'formal_parameters', 'parameter_property'}:
                params = []
                for param in child.children:
                    match param.type:
                        case 'identifier':
                            params.append(param.text.decode('utf-8'))
                        case 'required_parameter' | 'optional_parameter':
                            name = next((c.text.decode('utf-8') for c in param.children if c.type in {'identifier', 'object_pattern', 'array_pattern'}), None)
                            if name:
                                params.append(name)
                        case 'assignment_pattern':
                            name = next((c.text.decode('utf-8') for c in param.children if c.type == 'identifier'), None)
                            if name:
                                params.append(name)
                        case 'rest_pattern':
                            name = next((c.text.decode('utf-8') for c in param.children if c.type == 'identifier'), None)
                            if name:
                                params.append(f'...{name}')
                        case 'object_pattern' | 'array_pattern':
                            params.append(param.text.decode('utf-8')[:20])
                return f"({', '.join(params)})"
        return '()'

    def find_top_level_functions(self, root: Node) -> list[tuple[str, Node]]:
        top_level_functions = []
        class_node_types = self.get_class_node_types()
        standard_method_types = {'function_declaration', 'generator_function'}

        def walk(node: Node):
            if node.type in class_node_types:
                return

            if node.type in standard_method_types:
                name = self.extract_name(node)
                top_level_functions.append((name, node))
                return

            if node.type == 'variable_declarator':
                name = None
                arrow_node = None
                for child in node.children:
                    if child.type == 'identifier':
                        name = child.text.decode('utf-8')
                    elif child.type == 'arrow_function':
                        arrow_node = child
                if name and arrow_node:
                    top_level_functions.append((name, arrow_node))
                    return

            for child in node.children:
                walk(child)

        walk(root)
        return top_level_functions

    def find_class_methods(self, class_node: Node) -> list[tuple[str, Node]]:
        methods = []
        body_node = next((c for c in class_node.children if c.type == 'class_body'), None)
        if not body_node:
            return methods

        for child in body_node.children:
            if child.type == "method_definition":
                name = self.extract_name(child)
                methods.append((name, child))
            elif child.type == "field_definition":
                arrow_node = next((c for c in child.children if c.type == "arrow_function"), None)
                if arrow_node:
                    name_node = child.child_by_field_name("property")
                    name = name_node.text.decode("utf-8") if name_node else "unknown"
                    methods.append((name, arrow_node))

        return methods

    def extract_semantic_units(self, node: Node, class_node: Node | None = None) -> CodeSemanticUnits:
        units = CodeSemanticUnits()

        if class_node is not None:
            units.class_name = self.extract_name(class_node)
            units.class_comment = self._get_preceding_comment(class_node)
            units.class_attributes = self._extract_js_ts_attributes(class_node)

        if node.type in self.get_method_node_types():
            units.method_name = self.extract_name(node)
            units.method_comment = self._get_preceding_comment(node)
            units.method_params = self._extract_param_names(node)
            units.return_type = self._extract_return_type(node)

        return units

    def _get_preceding_comment(self, node: Node) -> str | None:
        """Safely walks backward to extract comments, checking parent wrappers for exports."""
        target = node
        if node.parent and node.parent.type in {"export_statement", "lexical_declaration", "variable_declaration"}:
            target = node.parent

        prev = target.prev_sibling
        comments = []
        while prev is not None:
            if prev.type == "comment":
                comments.insert(0, prev.text.decode("utf-8"))
            elif prev.type not in {"export", "default"}:
                break
            prev = prev.prev_sibling
        return "\n".join(comments) if comments else None

    def _extract_js_ts_attributes(self, class_node: Node) -> list[str]:
        """Comprehensive extractor capturing inline fields, constructor assignments, and interfaces."""
        attributes = set()

        if class_node.type == 'interface_declaration':
            body = next((c for c in class_node.children if c.type == 'object_type'), None)
            if body:
                for child in body.children:
                    if child.type == 'property_signature':
                        name_node = child.child_by_field_name('name')
                        if name_node:
                            attributes.add(name_node.text.decode('utf-8'))
            return sorted(list(attributes))

        body_node = next((c for c in class_node.children if c.type in {'class_body', 'enum_body'}), None)
        if not body_node:
            return []

        for child in body_node.children:
            if child.type == 'field_definition':
                name_node = child.child_by_field_name('property') or next((c for c in child.children if c.type == 'property_identifier'), None)
                if name_node:
                    attributes.add(name_node.text.decode('utf-8'))

            elif child.type == 'property_identifier':
                attributes.add(child.text.decode('utf-8'))

            elif child.type == 'method_definition':
                name_node = child.child_by_field_name('name')
                if name_node and name_node.text.decode('utf-8') == 'constructor':
                    params = next((c for c in child.children if c.type == 'formal_parameters'), None)
                    if params:
                        for p in params.children:
                            if p.type == 'parameter_property':
                                p_name = next((sub for sub in p.children if sub.type == 'identifier'), None)
                                if p_name:
                                    attributes.add(p_name.text.decode('utf-8'))

                    def find_this_assignments(n: Node):
                        if n.type == 'assignment_expression':
                            left = n.child_by_field_name('left')
                            if left and left.type == 'member_expression':
                                obj = left.child_by_field_name('object')
                                prop = left.child_by_field_name('property')
                                if obj and obj.text.decode('utf-8') == 'this' and prop:
                                    attributes.add(prop.text.decode('utf-8'))
                        for c in n.children:
                            find_this_assignments(c)

                    find_this_assignments(child)

        return sorted(list(attributes))

    def _extract_param_names(self, node: Node) -> list[str]:
        """Isolates and outputs uniform flat lists of plain clean variable parameter names."""
        if node.type == 'arrow_function':
            for child in node.children:
                if child.type == 'identifier':
                    return [child.text.decode('utf-8')]

        for child in node.children:
            if child.type in {'formal_parameters', 'parameter_property'}:
                names = []

                def collect_identifiers(n: Node):
                    if n.type == 'identifier':
                        names.append(n.text.decode('utf-8'))
                    elif n.type == 'pair':
                        value_node = n.child_by_field_name('value')
                        if value_node:
                            collect_identifiers(value_node)
                    elif n.type in {'required_parameter', 'optional_parameter', 'assignment_pattern', 'rest_pattern', 'object_pattern', 'array_pattern'}:
                        for c in n.children:
                            collect_identifiers(c)

                for param in child.children:
                    if param.type == 'identifier':
                        names.append(param.text.decode('utf-8'))
                    else:
                        collect_identifiers(param)
                return names
        return []

    def _extract_return_type(self, node: Node) -> str | None:
        """Pulls TypeScript type annotation signatures from functions or methods."""
        type_node = node.child_by_field_name('return_type')
        if type_node:
            return type_node.text.decode('utf-8').strip(': \n\t')
        return None