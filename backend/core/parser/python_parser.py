import tree_sitter_python as tspython
from tree_sitter import Language, Node
from core.parser.base import BaseParser
from core.schemas import CodeSemanticUnits


class PythonParser(BaseParser):

    def get_language(self) -> Language:
        return Language(tspython.language())

    def get_class_node_types(self) -> list[str]:
        return ['class_definition']

    def get_method_node_types(self) -> list[str]:
        return ['function_definition', 'decorated_definition']

    def extract_name(self, node: Node) -> str:
        if node.type == 'decorated_definition':
            for child in node.children:
                if child.type == 'function_definition':
                    return self.extract_name(child)
                
        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')
        
        for child in node.children:
            if child.type == 'identifier':
                return child.text.decode('utf-8')
        return 'unknown'

    def extract_params(self, node: Node) -> str:
        if node.type == 'decorated_definition':
            for child in node.children:
                if child.type == 'function_definition':
                    return self.extract_params(child)
                
        for child in node.children:
            if child.type == 'parameters':
                params = []
                for param in child.children:
                    # 1. Simple parameters
                    if param.type == 'identifier' and param.text.decode('utf-8') != 'self':
                        params.append(param.text.decode('utf-8'))

                    # 2. Complex parameters (typed, default, typed with default)
                    elif param.type in {'typed_parameter', 'default_parameter', 'typed_default_parameter', 'keyword_argument'}:
                        name = next((c.text.decode('utf-8') for c in param.children if c.type == 'identifier'), None)
                        if name and name != 'self':
                            params.append(name)

                    # 3. Positional-only indicator - def foo(a, b, /, c, d)
                    elif param.type == 'positional_separator':
                        params.append('/')

                    # 4. Keyword-only indicator - def foo(a, b, *, c, d)
                    elif param.type == 'keyword_separator':
                        params.append('*')

                    # 5. List splats (*args)
                    elif param.type in {'list_splat_pattern', 'splat_parameter'}:
                        name = next((c.text.decode('utf-8') for c in param.children if c.type == 'identifier'), None)
                        if name:
                            params.append(f"*{name}")
                        else: 
                            params.append('*') # Edge case for * without a name

                    # 6. Dictionary splats (**kwargs)
                    elif param.type in {'dictionary_splat_pattern', 'keyword_splat_parameter'}:
                        name = next((c.text.decode('utf-8') for c in param.children if c.type == 'identifier'), None)
                        if name:
                            params.append(f"**{name}")
                            
                return f"({', '.join(params)})"
        return '()'

    def extract_semantic_units(self, node: Node, class_node: Node | None = None) -> CodeSemanticUnits:
        units = CodeSemanticUnits()

        if class_node is not None:
            units.class_name = self.extract_name(class_node)
            units.class_comment = self._get_python_documentation(class_node)
            units.class_attributes = self._extract_field_names(class_node)

        # Ensure we drop into decorated items to pull properties cleanly
        actual_method_node = node
        if node.type == 'decorated_definition':
            actual_method_node = next((c for c in node.children if c.type == 'function_definition'), node)

        if node.type in self.get_method_node_types():
            units.method_name = self.extract_name(actual_method_node)
            units.method_comment = self._get_python_documentation(actual_method_node)
            units.method_params = self._extract_param_names(actual_method_node)
            units.return_type = self._extract_return_type(actual_method_node)

        return units

    def _get_python_documentation(self, node: Node) -> str | None:
        """Extracts Python docstrings (internal string literals) as primary documentation."""
        body = node.child_by_field_name('body')
        if not body:
            return None
            
        # The first child expression under a body code block is usually the docstring
        first_expr = next((c for c in body.children if c.type == 'expression_statement'), None)
        if first_expr and first_expr.children and first_expr.children[0].type == 'string':
            raw_doc = first_expr.children[0].text.decode('utf-8')
            # Clean off the triple quotes (''' or """)
            return raw_doc.strip('"\' \n\t')
            
        return None

    def _extract_field_names(self, class_node: Node) -> list[str]:
        """Collects class variables and self.attributes from local scopes."""
        attributes = set()
        body = class_node.child_by_field_name('body')
        if not body:
            return []

        # Walk through class-level contents
        for child in body.children:
            # 1. Class-level Variables (e.g., max_tokens = 512)
            if child.type == 'expression_statement':
                assign = next((c for c in child.children if c.type == 'assignment'), None)
                if assign:
                    left = assign.child_by_field_name('left')
                    if left and left.type == 'identifier':
                        attributes.add(left.text.decode('utf-8'))
                        
            # 2. Instance variables inside functions (e.g., self.model = model)
            elif child.type in self.get_method_node_types():
                func_node = child
                if child.type == 'decorated_definition':
                    func_node = next((c for c in child.children if c.type == 'function_definition'), child)
                    
                func_body = func_node.child_by_field_name('body')
                if func_body:
                    # Recursive search inside function lines looking for attribute settings
                    def find_self_assignments(n: Node):
                        if n.type == 'assignment':
                            left = n.child_by_field_name('left')
                            # Structures match: self.variable -> an attribute node
                            if left and left.type == 'attribute':
                                obj = left.child_by_field_name('object')
                                prop = left.child_by_field_name('attribute')
                                if obj and obj.text.decode('utf-8') == 'self' and prop:
                                    attributes.add(prop.text.decode('utf-8'))
                        for sub_child in n.children:
                            find_self_assignments(sub_child)
                            
                    find_self_assignments(func_body)

        return sorted(list(attributes))

    def _extract_param_names(self, node: Node) -> list[str]:
        """Isolates clean parameter strings clear of typing syntax indicators."""
        for child in node.children:
            if child.type == 'parameters':
                names = []
                for param in child.children:
                    # Filter identifiers skipping self
                    if param.type == 'identifier' and param.text.decode('utf-8') != 'self':
                        names.append(param.text.decode('utf-8'))
                    elif param.type in {'typed_parameter', 'default_parameter', 'typed_default_parameter', 'keyword_argument', 'list_splat_pattern', 'splat_parameter', 'dictionary_splat_pattern', 'keyword_splat_parameter'}:
                        name_node = next((c for c in param.children if c.type == 'identifier'), None)
                        if name_node and name_node.text.decode('utf-8') != 'self':
                            names.append(name_node.text.decode('utf-8'))
                return names
        return []

    def _extract_return_type(self, node: Node) -> str | None:
        """Extracts type-hinted arrow returns from functions."""
        return_node = node.child_by_field_name('return_type')
        if return_node:
            return return_node.text.decode('utf-8').strip('-> ')
        return None
