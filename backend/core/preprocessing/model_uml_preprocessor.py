import logging
from xml.etree import ElementTree

from core.schemas import Artifact, Element, ElementLevel, ModelSemanticUnits
from core.preprocessing.base import Preprocessor

logger = logging.getLogger(__name__)

XMI_NAMESPACE = "http://www.omg.org/spec/XMI/20131001"
XMI_TYPE = f"{{{XMI_NAMESPACE}}}type"
XMI_ID = f"{{{XMI_NAMESPACE}}}id"
XMI_IDREF = f"{{{XMI_NAMESPACE}}}idref"


class ModelUmlPreprocessor(Preprocessor):
    """Splits a UML model into its components and interfaces.

    Only components are compared: an interface describes how components talk to
    each other, so it is context for their text rather than a link target of
    its own.
    """

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            elements.append(Element(
                identifier=artifact.identifier,
                type=artifact.type,
                content=artifact.content,
                granularity=0,
                level=ElementLevel.ARTIFACT,
                parent_id=None,
                compare=False
            ))
            elements += self._model_elements(artifact)
        return elements

    def _model_elements(self, artifact: Artifact) -> list[Element]:
        nodes = self._typed_nodes(artifact)
        if nodes is None:
            return []

        interfaces = {n.get(XMI_ID): n for n in nodes if self._uml_type(n) == "Interface"}
        components = [n for n in nodes if self._uml_type(n) == "Component"]
        usages = [n for n in nodes if self._uml_type(n) == "Usage"]
        # A realization is normally owned by its component, but some exporters
        # write it alongside instead, pointing back with a client reference.
        realizations = [n for n in nodes if self._uml_type(n) == "InterfaceRealization"]

        elements = []
        # Components first, so adding an interface to a model does not
        # renumber every component that already existed.
        for counter, component in enumerate(components):
            provided = self._provided(component, interfaces, realizations)
            required = self._required(component, interfaces, usages)
            elements.append(self._element(
                artifact, counter, component, ElementLevel.COMPONENT, compare=True,
                content=self._component_text(component, provided, required),
                # The same relationships the text describes, kept structured so
                # a diagram does not have to read the text back.
                model_units=ModelSemanticUnits(
                    name=self._name(component),
                    provides=[self._name(i) for i in provided],
                    requires=[self._name(i) for i in required],
                )
            ))

        for counter, interface in enumerate(interfaces.values(), start=len(components)):
            elements.append(self._element(
                artifact, counter, interface, ElementLevel.INTERFACE, compare=False,
                content=self._interface_text(interface),
                model_units=ModelSemanticUnits(name=self._name(interface))
            ))

        return elements

    def _typed_nodes(self, artifact: Artifact) -> list | None:
        """Every node in the model that declares a UML type.

        Searched over the whole tree rather than a fixed wrapper tag: models
        nest elements inside packages, and exporters disagree on the tag name.
        """
        try:
            root = ElementTree.fromstring(artifact.content)
        except ElementTree.ParseError as error:
            logger.warning(f"Could not parse {artifact.identifier} as UML XML: {error}")
            return None
        return [node for node in root.iter() if node.get(XMI_TYPE)]

    def _element(self, artifact, counter, node, level, compare, content, model_units) -> Element:
        return Element(
            identifier=f"{artifact.identifier}${counter}${node.get(XMI_ID)}",
            type=artifact.type,
            content=content,
            granularity=1,
            level=level,
            parent_id=artifact.identifier,
            compare=compare,
            model_units=model_units
        )

    def _component_text(self, component, provided: list, required: list) -> str:
        lines = [f"Type: Component, Name: {self._name(component)}"]

        for interface in provided:
            lines.append(f"Interface Realization: {self._name(interface)}")
            for operation in interface.findall("ownedOperation"):
                lines.append(f"Operation: {self._name(operation)}")

        for interface in required:
            lines.append(f"Uses: {self._name(interface)}")

        return "\n".join(lines)

    def _interface_text(self, interface) -> str:
        lines = [f"Type: Interface, Name: {self._name(interface)}"]
        for operation in interface.findall("ownedOperation"):
            lines.append(f"Operation: {self._name(operation)}")
        return "\n".join(lines)

    def _provided(self, component, interfaces: dict, realizations: list) -> list:
        """Interfaces the component realizes, owned or written alongside."""
        owned = component.findall("interfaceRealization")
        component_id = component.get(XMI_ID)
        alongside = [
            r for r in realizations
            if component_id in self._references(r, "client")
        ]

        found = []
        for realization in owned + alongside:
            targets = (self._references(realization, "contract")
                       or self._references(realization, "supplier"))
            found += [interfaces[t] for t in targets if t in interfaces]
        return found

    def _required(self, component, interfaces: dict, usages: list) -> list:
        """Interfaces the component depends on, via Usage relationships."""
        component_id = component.get(XMI_ID)
        found = []
        for usage in usages:
            if component_id not in self._references(usage, "client"):
                continue
            targets = self._references(usage, "supplier")
            found += [interfaces[t] for t in targets if t in interfaces]
        return found

    def _references(self, node, name: str) -> list[str]:
        """The ids a UML reference points at, however it was serialised.

        XMI allows the same reference as an attribute holding one or more ids,
        or as nested children carrying xmi:idref - or href when the target
        lives in another file.
        """
        ids = (node.get(name) or "").split()
        for child in node.findall(name):
            reference = child.get(XMI_IDREF) or child.get("href") or ""
            # href reads "Other.uml#_id"; only the fragment names the element.
            ids.append(reference.rsplit("#", 1)[-1])

        # Both forms in one file would otherwise list the interface twice.
        return list(dict.fromkeys(i for i in ids if i))

    def _uml_type(self, node) -> str:
        return (node.get(XMI_TYPE) or "").removeprefix("uml:")

    def _name(self, node) -> str:
        return node.get("name") or node.get(XMI_ID) or "unnamed"
