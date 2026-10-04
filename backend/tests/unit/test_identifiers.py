"""How an element is named, and when two pieces of text count as the same."""

import os
from pathlib import Path

from core.content import content_hash, relative_identifier
from core.ingestion import CodeProvider, DocumentProvider
from tests.helpers import copy_corpus
from tests.recorder import case


@case(
    id="U-10",
    feature="Identifiers / relative_identifier",
    level="unit",
    priority="Critical",
    why="Pinned links are matched by identifier across runs. This mismatch already broke pinning twice, silently.",
    input="Real identifiers from DocumentProvider (relative) and CodeProvider (absolute) over the same workspace",
    expected="Both reduce to workspace-relative paths with '/' separators; the '::member' part is unchanged",
)
def test_document_and_code_identifiers_reduce_to_one_form(tmp_path, record):
    """The two providers name elements differently; the stored form must be the same for both."""
    source, target = copy_corpus(tmp_path)
    roots = [source, target]

    document = DocumentProvider(str(source)).load()[0].identifier
    code = next(a.identifier for a in CodeProvider(str(target)).load() if "Auth" in a.identifier)
    record(f"document provider gives: {document}")
    record(f"code provider gives: {code}")
    assert not os.path.isabs(document) and os.path.isabs(code)

    method = f"{code}::Auth::login(String name, String passphrase)"
    reduced = relative_identifier(method, roots)
    record(f"stored form of the document: {relative_identifier(document, roots)}")
    record(f"stored form of the method: {reduced}")

    assert relative_identifier(document, roots) == "UC1.txt"
    assert reduced == "Auth.java::Auth::login(String name, String passphrase)"
    # Applying it to something already in the stored form changes nothing.
    assert relative_identifier(reduced, roots) == reduced


@case(
    id="U-11",
    feature="Identifiers / relative_identifier",
    level="unit",
    priority="High",
    why="A root that is only a name-prefix of another folder must not be stripped, or two files would get one identifier.",
    input="A nested file, a file in a sibling folder whose name starts with the root's name, and a path outside every root",
    expected="Nested -> 'pkg/sub/A.java'; sibling and outside paths returned exactly as given",
)
def test_only_paths_inside_the_workspace_are_rewritten(tmp_path, record):
    """A path is shortened only when it is really inside a workspace root."""
    root = tmp_path / "target"
    nested = str(root / "pkg" / "sub" / "A.java")
    sibling = str(tmp_path / "target2" / "A.java")
    outside = str(Path("C:/elsewhere/A.java")) + "::A::run()"

    results = {name: relative_identifier(value, [root]) for name, value in
               (("nested", nested), ("sibling", sibling), ("outside", outside))}
    record(results)

    assert results["nested"] == "pkg/sub/A.java"
    assert results["sibling"] == sibling
    assert results["outside"] == outside
    # Roots given as plain strings behave the same as Path objects.
    assert relative_identifier(nested, [str(root)]) == "pkg/sub/A.java"


@case(
    id="U-12",
    feature="Identifiers / content_hash",
    level="unit",
    priority="High",
    why="The graph and the caches decide 'unchanged' by this hash; a formatter run must not look like a rewrite.",
    input="A method, the same method with trailing spaces and blank lines added, and the method with one word changed",
    expected="First two hashes equal; the third different",
)
def test_formatting_does_not_change_the_hash(record):
    """Whitespace a reader would not notice does not change what the content is."""
    original = "void logout() {\n    this.clear();\n}"
    reformatted = "void logout() {   \n\n    this.clear();  \n\n}\n"
    edited = "void logout() {\n    this.reset();\n}"

    hashes = [content_hash(text)[:12] for text in (original, reformatted, edited)]
    record(f"original {hashes[0]}, reformatted {hashes[1]}, edited {hashes[2]}")

    assert content_hash(original) == content_hash(reformatted)
    assert content_hash(original) != content_hash(edited)
