"""Splitting artifacts into elements, and rolling links up to the level asked for."""

from core.classification.base import ClassificationResult
from core.dependency.base import CodeDependency, DependencyType
from core.dependency.expander import DependencyLinkExpander
from core.dependency.graph import DependencyGraph
from core.output.result_aggregator import ResultAggregator
from core.preprocessing import (
    ArtifactPreprocessor, CodeMethodPreprocessor, ModelUmlPreprocessor, SentencePreprocessor,
)
from core.schemas import Artifact, Element, ElementLevel
from core.storage.chroma_store import collection_names
from tests.helpers import DATA
from tests.recorder import case


def artifact(name: str, kind: str = "requirement") -> Artifact:
    return Artifact(identifier=name, type=kind, content=(DATA / name).read_text(encoding="utf-8"))


def element(identifier, level, parent=None, compare=True, granularity=0) -> Element:
    return Element(
        identifier=identifier, type="t", content=identifier, level=level,
        parent_id=parent, compare=compare, granularity=granularity,
    )


def summary(elements) -> list[str]:
    return [f"{e.identifier} [{e.level.value}, compare={e.compare}]" for e in elements]


@case(
    id="U-14",
    feature="Preprocessing / CodeMethodPreprocessor",
    level="unit",
    priority="High",
    why="Methods are what links are found on. Wrong identifiers or parents break rolling a link up to its class or file.",
    input="tests/data/code/Auth.java: one class with login, logout and clear",
    expected="1 file and 1 class (not compared), 3 methods (compared); each method's parent is the class, the class's parent is the file",
)
def test_java_file_becomes_file_class_and_methods(record):
    """A Java file is split into a file, its class, and one element per method."""
    elements = CodeMethodPreprocessor().preprocess([artifact("code/Auth.java", "source code")])
    record(summary(elements))

    by_level = {level: [e for e in elements if e.level == level] for level in ElementLevel}
    file, = by_level[ElementLevel.FILE]
    owner, = by_level[ElementLevel.CLASS]
    methods = by_level[ElementLevel.FUNCTION]

    assert not file.compare and not owner.compare
    assert owner.identifier == "code/Auth.java::Auth" and owner.parent_id == file.identifier
    assert sorted(m.identifier.split("(")[0] for m in methods) == [
        "code/Auth.java::Auth::clear", "code/Auth.java::Auth::login", "code/Auth.java::Auth::logout",
    ]
    assert all(m.compare and m.parent_id == owner.identifier for m in methods)


@case(
    id="U-16",
    feature="Preprocessing / ResultAggregator",
    level="unit",
    priority="High",
    why="Users choose to see links per file. Two methods of one file must become one link, not two, and not a link on the wrong file.",
    input="Three method-level results for one requirement: A.m1 (0.9), A.m2 (0.6) and B.m1 (0.5); target level FILE",
    expected="Two links: req -> A.java at 0.9 and req -> B.java at 0.5; with no target level, all three stay as they are",
)
def test_method_links_roll_up_to_their_file(record):
    """Links are reported on the ancestor at the requested level, one per pair."""
    requirement = element("req", ElementLevel.ARTIFACT)
    targets = [
        element("A.java", ElementLevel.FILE, compare=False),
        element("A.java::A", ElementLevel.CLASS, parent="A.java", compare=False, granularity=1),
        element("A.java::A::m1", ElementLevel.FUNCTION, parent="A.java::A", granularity=2),
        element("A.java::A::m2", ElementLevel.FUNCTION, parent="A.java::A", granularity=2),
        element("B.java", ElementLevel.FILE, compare=False),
        element("B.java::m1", ElementLevel.FUNCTION, parent="B.java", granularity=1),
    ]
    by_id = {e.identifier: e for e in targets}
    results = [
        ClassificationResult(source=requirement, target=by_id[name], confidence=score)
        for name, score in (("A.java::A::m1", 0.9), ("A.java::A::m2", 0.6), ("B.java::m1", 0.5))
    ]

    rolled = ResultAggregator(target_level=ElementLevel.FILE).aggregate([requirement], targets, results)
    as_found = ResultAggregator().aggregate([requirement], targets, results)
    record(f"at file level: {[(l.target_id, l.confidence, l.confidence_level.value) for l in rolled]}")
    record(f"with no level: {[l.target_id for l in as_found]}")

    assert [(l.target_id, l.confidence) for l in rolled] == [("A.java", 0.9), ("B.java", 0.5)]
    assert rolled[0].confidence_level.value == "high" and rolled[1].confidence_level.value == "low"
    assert len(as_found) == 3


@case(
    id="U-13",
    feature="Preprocessing / document preprocessors",
    level="unit",
    priority="Medium",
    why="These decide what a requirement element is. One file must be one element, or one per sentence, with stable identifiers.",
    input="tests/data/arch.txt (two sentences) through ArtifactPreprocessor and SentencePreprocessor",
    expected="Whole document: 1 compared element. Sentences: the document (not compared) plus 2 compared sentences named '::sentence_0' and '::sentence_1'",
)
def test_documents_split_whole_or_by_sentence(record):
    """A document is read as one element, or as one element per sentence under it."""
    document = artifact("arch.txt")

    whole = ArtifactPreprocessor().preprocess([document])
    sentences = SentencePreprocessor().preprocess([document])
    record(f"whole document: {summary(whole)}")
    record(f"by sentence: {summary(sentences)}")

    assert [(e.level, e.compare) for e in whole] == [(ElementLevel.ARTIFACT, True)]
    assert [e.identifier for e in sentences] == [
        "arch.txt", "arch.txt::sentence_0", "arch.txt::sentence_1",
    ]
    assert [e.compare for e in sentences] == [False, True, True]
    assert all(e.parent_id == "arch.txt" for e in sentences[1:])


@case(
    id="U-15",
    feature="Preprocessing / ModelUmlPreprocessor",
    level="unit",
    priority="Medium",
    why="The architecture diagram and the links both come from this: a component's provided and required interfaces must be right.",
    input="tests/data/model.uml: Authentication provides IAuth; Lending provides ILending and uses IAuth",
    expected="2 compared components with those interfaces and their operations in the text; 2 interfaces, not compared",
)
def test_uml_model_becomes_components_and_interfaces(record):
    """A UML model is split into components, each knowing what it provides and requires."""
    elements = ModelUmlPreprocessor().preprocess([artifact("model.uml", "architecture model")])
    components = {e.model_units.name: e for e in elements if e.level == ElementLevel.COMPONENT}
    interfaces = [e for e in elements if e.level == ElementLevel.INTERFACE]
    for name, component in components.items():
        record(f"{name}: provides {component.model_units.provides}, requires {component.model_units.requires}")
    record(f"interfaces: {[e.model_units.name for e in interfaces]}")

    assert components["Authentication"].model_units.provides == ["IAuth"]
    assert components["Lending"].model_units.provides == ["ILending"]
    assert components["Lending"].model_units.requires == ["IAuth"]
    assert "Operation: login" in components["Authentication"].content
    assert all(c.compare for c in components.values())
    assert len(interfaces) == 2 and not any(i.compare for i in interfaces)
    # A file that is not XML is skipped rather than failing the run.
    broken = Artifact(identifier="bad.uml", type="architecture model", content="not xml")
    assert len(ModelUmlPreprocessor().preprocess([broken])) == 1


@case(
    id="U-17",
    feature="Dependency expansion / DependencyLinkExpander",
    level="unit",
    priority="Medium",
    why="Expansion multiplies links. It must stop at the depth asked for and must not grow weak links into more weak links.",
    input="Call chain a -> b -> c. A strong link (0.9) and a weak link (0.3) to a, expanded at depth 1 and depth 2",
    expected="Depth 1: strong link reaches b only. Depth 2: reaches b and c with decayed confidence. The weak link is never expanded",
)
def test_expansion_respects_depth_and_minimum_confidence(record):
    """Links follow call edges up to the depth given, losing confidence with each step."""
    a, b, c = (element(name, ElementLevel.FUNCTION) for name in "abc")
    graph = DependencyGraph([a, b, c])
    graph.add_edges([
        CodeDependency(source_id="a", target_id="b", dependency_type=DependencyType.CALLS),
        CodeDependency(source_id="b", target_id="c", dependency_type=DependencyType.CALLS),
    ])
    strong = ClassificationResult(source=element("strong", ElementLevel.ARTIFACT), target=a, confidence=0.9)
    weak = ClassificationResult(source=element("weak", ElementLevel.ARTIFACT), target=a, confidence=0.3)

    def reached(depth):
        expanded = DependencyLinkExpander(graph, max_depth=depth).expand([strong, weak])
        return {(r.source.identifier, r.target.identifier): round(r.confidence, 3) for r in expanded}

    one, two = reached(1), reached(2)
    record(f"depth 1: {one}")
    record(f"depth 2: {two}")

    assert set(one) == {("strong", "a"), ("weak", "a"), ("strong", "b")}
    assert set(two) == set(one) | {("strong", "c")}
    assert two[("strong", "b")] == 0.765 and two[("strong", "c")] == 0.65


@case(
    id="U-18",
    feature="Vector store / collection_names",
    level="unit",
    priority="Medium",
    why="Each side's elements live in a collection. If both sides shared one, an element would be offered as a candidate for itself.",
    input="requirements -> code, and requirements -> requirements",
    expected="Two different collection names in both cases, each named after its kind",
)
def test_each_side_gets_its_own_collection(record):
    """The two sides of a run never share a collection, even when they hold the same kind."""
    different = collection_names("requirements", "code")
    same = collection_names("requirements", "requirements")
    record(f"requirements -> code: {different}")
    record(f"requirements -> requirements: {same}")

    assert different == ("requirements_elements", "code_elements")
    assert same[0] != same[1] and all(name.startswith("requirements") for name in same)


# Two plugins in one file, each with its own Plugin(option) - the shape of
# bootstrap.js, which is where this was found.
SAME_NAME_TWICE = """
(function () {
  function Plugin(option) { return option + 1; }
  window.alert = Plugin;
})();
(function () {
  function Plugin(option) { return option + 2; }
  window.modal = Plugin;
})();
function unique(value) { return value; }
"""


@case(
    id="U-58",
    feature="Preprocessing / functions that share a name",
    level="unit",
    priority="High",
    why="An identifier names exactly one element. Two functions with one name and one parameter list in a file - in different scopes, or in minified code - got the same identifier, and the vector index refused the whole run.",
    preconditions="None",
    input="A JavaScript file with Plugin(option) defined twice, in two scopes, and one function defined once; then a full pipeline run with that file among the code",
    expected="Every identifier is unique: the first keeps its name, the second ends '#2', and the function defined once is unchanged. The pipeline run finishes",
)
def test_functions_sharing_a_name_get_their_own_identifiers(tmp_path, record):
    """A second function with the same name and parameters is numbered instead of repeating an identifier."""
    elements = CodeMethodPreprocessor().preprocess(
        [Artifact(identifier="app.js", type="source code", content=SAME_NAME_TWICE)]
    )
    functions = [e.identifier for e in elements if e.level == ElementLevel.FUNCTION]
    record(f"functions: {functions}")

    from tests.helpers import build_pipeline, copy_corpus
    source, target = copy_corpus(tmp_path)
    (target / "app.js").write_text(SAME_NAME_TWICE, encoding="utf-8")
    result = build_pipeline(source, target, tmp_path / "chroma").run()
    stored = [e for e in result.target_elements if "app.js::" in e.identifier]
    record(f"pipeline run finished with {len(result.trace_links)} links; app.js functions indexed: {len(stored)}")

    assert functions == ["app.js::Plugin(option)", "app.js::Plugin(option)#2", "app.js::unique(value)"]
    assert len({e.identifier for e in elements}) == len(elements)
    assert len(stored) == 3
