"""What changed between two file sets of one side, element by element, and where each element went.

The diff runs on real preprocessor output, because the problem it solves is the
preprocessors' own: a sentence or a UML component is named by its position, so
one insertion renames everything after it.
"""

from core.preprocessing import CodeMethodPreprocessor, ModelUmlPreprocessor, SentencePreprocessor
from core.projects.diff import (
    IdMap, SideChanges, chain_renames, diff_elements, diff_files, items_from_elements, net_summary,
    translate_pins,
)
from core.schemas import Artifact
from tests.helpers import DATA
from tests.recorder import case

LOGIN = "A visitor signs in with a name and a password."
LOGOUT = "A signed-in visitor can sign out at any time."
BORROW = "A member borrows a title for three weeks."
EXTRA = "Every account is locked after five failed attempts."


def items(preprocessor, files: dict[str, str], kind: str = "requirement"):
    """One side's elements, as the diff reads them."""
    artifacts = [Artifact(identifier=path, type=kind, content=text) for path, text in files.items()]
    return items_from_elements(preprocessor.preprocess(artifacts))


def sentences(*texts: str) -> list:
    return items(SentencePreprocessor(), {"UC1.txt": " ".join(texts)})


def described(changes) -> dict:
    """The changes to compared elements as plain lists, for the report and for comparing."""
    return {
        "added": changes.added, "removed": changes.removed,
        "modified": changes.modified, "moved": changes.moved,
    }


@case(
    id="U-29",
    feature="Element diff / a sentence inserted in the middle",
    level="unit",
    priority="Critical",
    why="Sentences are named by position. One new sentence renames every sentence after it, which without the id map reads as every later link lost and found again, and offers pins to the wrong sentences.",
    input="UC1.txt with 3 sentences (login, logout, borrow); then the same file with a new sentence inserted between login and logout",
    expected="1 sentence added (the new sentence_1); nothing removed or modified; logout and borrow moved from sentence_1 and sentence_2 to sentence_2 and sentence_3; login keeps its identifier",
)
def test_sentence_inserted_in_the_middle(record):
    """An inserted sentence is the only addition; the sentences after it moved, not changed."""
    changes = diff_elements(sentences(LOGIN, LOGOUT, BORROW), sentences(LOGIN, EXTRA, LOGOUT, BORROW))
    record(described(changes))

    assert changes.added == ["UC1.txt::sentence_1"]
    assert changes.removed == [] and changes.modified == []
    assert changes.moved == [
        ("UC1.txt::sentence_1", "UC1.txt::sentence_2"),
        ("UC1.txt::sentence_2", "UC1.txt::sentence_3"),
    ]
    assert changes.id_map.translate("UC1.txt::sentence_0") == "UC1.txt::sentence_0"
    assert changes.meaningful


@case(
    id="U-30",
    feature="Element diff / a sentence removed",
    level="unit",
    priority="Critical",
    why="A removed sentence is what makes a link broken. The sentence after it takes over its position and identifier, so matching by identifier would wrongly say the removed sentence was edited.",
    input="UC1.txt with 3 sentences; then the same file without the middle one",
    expected="sentence_1 (logout) removed; borrow moved from sentence_2 to sentence_1; nothing added or modified; the removed identifier translates to nothing",
)
def test_sentence_removed(record):
    """A removed sentence is reported removed, and the one that slid into its place is reported moved."""
    changes = diff_elements(sentences(LOGIN, LOGOUT, BORROW), sentences(LOGIN, BORROW))
    record(described(changes))

    assert changes.removed == ["UC1.txt::sentence_1"]
    assert changes.moved == [("UC1.txt::sentence_2", "UC1.txt::sentence_1")]
    assert changes.added == [] and changes.modified == []
    assert changes.id_map.translate("UC1.txt::sentence_1") is None
    assert changes.id_map.translate("UC1.txt::sentence_2") == "UC1.txt::sentence_1"


@case(
    id="U-31",
    feature="Element diff / a sentence modified",
    level="unit",
    priority="Critical",
    why="An edited sentence is the same requirement reworded. It must read as modified - the reason a link's source changed - not as one removed and one added.",
    input="UC1.txt with 3 sentences; then the same file with the login sentence reworded ('password' -> 'passphrase')",
    expected="1 modified (sentence_0 -> sentence_0); nothing added, removed or moved. The whole-file element changed too, but only the compared sentences are listed",
)
def test_sentence_modified(record):
    """A reworded sentence is modified in place."""
    reworded = LOGIN.replace("password", "passphrase")
    changes = diff_elements(sentences(LOGIN, LOGOUT, BORROW), sentences(reworded, LOGOUT, BORROW))
    shown = changes.summary()
    record(described(changes))
    record(f"shown to the user: {shown}")

    assert ("UC1.txt::sentence_0", "UC1.txt::sentence_0") in changes.modified
    assert changes.added == [] and changes.removed == [] and changes.moved == []
    assert shown["modified"] == ["UC1.txt::sentence_0"]


@case(
    id="U-32",
    feature="Element diff / two sentences swapped",
    level="unit",
    priority="High",
    why="Reordering changes no text. Both sentences must keep their links by moving with them, rather than each link pointing at the other sentence.",
    input="UC1.txt with login then logout; then logout then login",
    expected="Both moved (sentence_0 <-> sentence_1); nothing added, removed or modified",
)
def test_two_sentences_swapped(record):
    """Swapped sentences are two moves, and no edit."""
    changes = diff_elements(sentences(LOGIN, LOGOUT), sentences(LOGOUT, LOGIN))
    record(described(changes))

    assert sorted(changes.moved) == [
        ("UC1.txt::sentence_0", "UC1.txt::sentence_1"),
        ("UC1.txt::sentence_1", "UC1.txt::sentence_0"),
    ]
    assert changes.added == [] and changes.removed == [] and changes.modified == []


@case(
    id="U-33",
    feature="File diff / renamed with the same content",
    level="unit",
    priority="Critical",
    why="An uploaded folder has no rename list. A file moved without being edited must still be recognised by its content, or every link on it breaks.",
    input="Files {UC1.txt, UC2.txt}; then UC2.txt moved to archive/UC2.txt unchanged and UC1.txt edited. Then the elements of the moved file, read under both paths",
    expected="UC1.txt modified; UC2.txt renamed to archive/UC2.txt; nothing added or removed. Both sentences moved to the same identifier under the new path, and the id map takes the file itself there too",
)
def test_file_renamed_with_the_same_hash(record):
    """A file whose content reappears under a new path is a rename, and its elements follow it."""
    files = diff_files({"UC1.txt": "a1", "UC2.txt": "b2"}, {"UC1.txt": "a9", "archive/UC2.txt": "b2"})
    elements = diff_elements(
        items(SentencePreprocessor(), {"UC2.txt": f"{LOGIN} {LOGOUT}"}),
        items(SentencePreprocessor(), {"archive/UC2.txt": f"{LOGIN} {LOGOUT}"}),
        renames=files.renamed,
    )
    record(f"files: added {files.added}, removed {files.removed}, modified {files.modified}, renamed {files.renamed}")
    record(described(elements))

    assert files.modified == ["UC1.txt"] and files.renamed == {"UC2.txt": "archive/UC2.txt"}
    assert files.added == [] and files.removed == []
    assert sorted(elements.moved) == [
        ("UC2.txt::sentence_0", "archive/UC2.txt::sentence_0"),
        ("UC2.txt::sentence_1", "archive/UC2.txt::sentence_1"),
    ]
    assert elements.id_map.translate("UC2.txt") == "archive/UC2.txt"
    assert elements.added == [] and elements.removed == [] and elements.modified == []


@case(
    id="U-34",
    feature="Element diff / whitespace-only change",
    level="unit",
    priority="Critical",
    why="Reformatting a file changes its bytes and its hash but not its meaning. It must not make a new version or be reported as an edit.",
    input="UC1.txt with 2 sentences; then the same text with extra spaces, a blank line and a trailing newline. And a Java file re-indented",
    expected="The file hashes differ, but no element is added, removed, modified or moved, and the change is not meaningful - for both files",
)
def test_whitespace_only_change_is_not_meaningful(record):
    """Only whitespace changed: the files differ, the elements do not."""
    before = f"{LOGIN} {LOGOUT}"
    after = f"  {LOGIN}\n\n   {LOGOUT}   \n"
    code = (DATA / "code" / "Auth.java").read_text(encoding="utf-8")
    reindented = "\n".join("\t" + line.strip() for line in code.splitlines())

    files = diff_files({"UC1.txt": "x"}, {"UC1.txt": "y"})
    text = diff_elements(sentences(before), items(SentencePreprocessor(), {"UC1.txt": after}))
    java = diff_elements(
        items(CodeMethodPreprocessor(), {"Auth.java": code}, "source code"),
        items(CodeMethodPreprocessor(), {"Auth.java": reindented}, "source code"),
    )
    record(f"file diff: modified {files.modified}")
    record(f"text: {described(text)}, meaningful {text.meaningful}")
    record(f"java: {described(java)}, meaningful {java.meaningful}")

    assert files.modified == ["UC1.txt"]
    assert not text.meaningful and not java.meaningful
    assert described(text) == described(java) == {"added": [], "removed": [], "modified": [], "moved": []}


@case(
    id="U-35",
    feature="Element diff / UML components named by a counter",
    level="unit",
    priority="Critical",
    why="A component's identifier carries its position among the components. A component added at the front renumbers every other one, which must read as moves - not as every component replaced.",
    input="tests/data/model.uml (Authentication, Lending); then the same model with a new Catalogue component placed before them",
    expected="1 component added (Catalogue, counter 0); Authentication moved from $0$ to $1$ and Lending from $1$ to $2; no component removed or modified",
)
def test_uml_components_with_counter_ids(record):
    """A new component shifts the counters of the others; they are moved, not changed."""
    model = (DATA / "model.uml").read_text(encoding="utf-8")
    catalogue = (
        '  <packagedElement xmi:type="uml:Component" xmi:id="_cat" name="Catalogue"/>\n'
        '  <packagedElement xmi:type="uml:Component" xmi:id="_auth"'
    )
    grown = model.replace('  <packagedElement xmi:type="uml:Component" xmi:id="_auth"', catalogue, 1)

    changes = diff_elements(
        items(ModelUmlPreprocessor(), {"model.uml": model}, "architecture model"),
        items(ModelUmlPreprocessor(), {"model.uml": grown}, "architecture model"),
    )
    shown = changes.summary()
    record(f"shown to the user: {shown}")

    assert shown["added"] == ["model.uml$0$_cat"]
    assert shown["moved"] == [
        {"old": "model.uml$0$_auth", "new": "model.uml$1$_auth"},
        {"old": "model.uml$1$_lend", "new": "model.uml$2$_lend"},
    ]
    assert shown["removed"] == [] and shown["modified"] == []


@case(
    id="U-36",
    feature="Element diff / methods keep their names",
    level="unit",
    priority="High",
    why="A method is named by its signature, not its position. One rewritten beyond recognition is still the same method, and a method deleted while an unrelated one is added must not be called an edit.",
    input="Auth.java with login, logout, clear; then login's body rewritten, clear deleted, and a new method audit added after logout",
    expected="login modified (same identifier); clear removed; audit added; logout unchanged",
)
def test_methods_are_matched_by_name_or_text(record):
    """A rewritten method stays itself; a deleted one and a new one stay apart."""
    code = (
        "public class Auth {\n"
        "    public boolean login(String name, String password) { return check(name, password); }\n"
        "    public void logout() { session = null; }\n"
        "    public void clear() { attempts = 0; }\n"
        "}\n"
    )
    changed = (
        "public class Auth {\n"
        "    public boolean login(String name, String password) { throw new Unsupported(); }\n"
        "    public void logout() { session = null; }\n"
        "    public void audit(String event) { log.write(event, clock.now()); }\n"
        "}\n"
    )
    changes = diff_elements(
        items(CodeMethodPreprocessor(), {"Auth.java": code}, "source code"),
        items(CodeMethodPreprocessor(), {"Auth.java": changed}, "source code"),
    )
    shown = changes.summary()
    record(f"shown to the user: {shown}")

    assert shown["modified"] == ["Auth.java::Auth::login(String name, String password)"]
    assert shown["removed"] == ["Auth.java::Auth::clear()"]
    assert shown["added"] == ["Auth.java::Auth::audit(String event)"]
    assert shown["moved"] == []


@case(
    id="U-37",
    feature="Id map / pins translated",
    level="unit",
    priority="Critical",
    why="Pins are re-offered to the classifier by identifier. Untranslated, a pin made on the old sentence_1 is offered for whatever sentence now sits at position 1.",
    preconditions="The diff of U-29 (sentence inserted) on the source side and of U-30's kind (logout() removed) on the target side",
    input="Pins {sentence_0 -> login(), sentence_1 -> logout(), sentence_2 -> borrow()} translated through both id maps",
    expected="{sentence_0 -> login(), sentence_3 -> borrow()}: the logout pin is dropped because its target is gone, and borrow's source follows it to sentence_3",
)
def test_pins_are_translated_through_the_id_map(record):
    """Pins follow moved elements and are dropped when an end was removed."""
    source = diff_elements(sentences(LOGIN, LOGOUT, BORROW), sentences(LOGIN, EXTRA, LOGOUT, BORROW)).id_map
    target = IdMap(moved={}, removed={"Auth.java::Auth::logout()"})
    pins = {
        "UC1.txt::sentence_0": {"Auth.java::Auth::login()"},
        "UC1.txt::sentence_1": {"Auth.java::Auth::logout()"},
        "UC1.txt::sentence_2": {"Loans.java::Loans::borrow()"},
    }

    translated = translate_pins(pins, source, target)
    record(f"before: { {s: sorted(t) for s, t in pins.items()} }")
    record(f"after: { {s: sorted(t) for s, t in translated.items()} }")

    assert translated == {
        "UC1.txt::sentence_0": {"Auth.java::Auth::login()"},
        "UC1.txt::sentence_3": {"Loans.java::Loans::borrow()"},
    }


@case(
    id="U-38",
    feature="Id map / stored and composed",
    level="unit",
    priority="High",
    why="The map is stored on a version and read back for the change report, which may span several versions. It must survive the round trip and chain correctly.",
    input="Version 2's map (sentence inserted at 1) and version 3's map (sentence_3 removed), stored as JSON-ready dicts, read back and composed",
    expected="Read back equal. Composed: sentence_1 -> sentence_2, sentence_2 -> gone (it became sentence_3, which version 3 removed), sentence_0 unchanged",
)
def test_id_maps_round_trip_and_compose(record):
    """Maps read back from storage are the maps that were stored, and chain across versions."""
    second = diff_elements(sentences(LOGIN, LOGOUT, BORROW), sentences(LOGIN, EXTRA, LOGOUT, BORROW)).id_map
    third = diff_elements(sentences(LOGIN, EXTRA, LOGOUT, BORROW), sentences(LOGIN, EXTRA, LOGOUT)).id_map
    stored = IdMap.from_dict(second.to_dict())
    chained = stored.then(IdMap.from_dict(third.to_dict()))
    record(f"version 2: {second.to_dict()}")
    record(f"version 3: {third.to_dict()}")
    record({f"sentence_{n}": chained.translate(f"UC1.txt::sentence_{n}") for n in range(3)})

    assert stored == second
    assert chained.translate("UC1.txt::sentence_0") == "UC1.txt::sentence_0"
    assert chained.translate("UC1.txt::sentence_1") == "UC1.txt::sentence_2"
    assert chained.translate("UC1.txt::sentence_2") is None


def composed(*maps: IdMap) -> IdMap:
    """Several versions' maps, applied one after another."""
    result = maps[0]
    for later in maps[1:]:
        result = result.then(later)
    return result


@case(
    id="U-48",
    feature="Id map / composed: insert, then remove at the same place",
    level="unit",
    priority="Critical",
    why="An identifier a later version removes names the element holding it after the earlier version, not the element that once had that name. Read the other way, a sentence that never went away is reported broken.",
    input="v2 inserts a sentence at s5, so the old s5 moves to s6. v3 removes the inserted sentence, so the old one moves back to s5",
    expected="v1's s5 translates to s5, and nothing is removed",
)
def test_insert_then_remove_at_the_same_place(record):
    """A sentence pushed down and back up again is still there under its old name."""
    chained = composed(IdMap(moved={"s5": "s6"}), IdMap(moved={"s6": "s5"}, removed={"s5"}))
    record(chained.to_dict())

    assert chained.translate("s5") == "s5"
    assert chained.removed == set()


@case(
    id="U-49",
    feature="Id map / composed: two swaps",
    level="unit",
    priority="High",
    why="Two swaps of the same pair put every element back where it was. Any leftover entry would send a link to the other element.",
    input="v2 swaps a and b; v3 swaps them back",
    expected="a translates to a and b to b; the composed map is empty",
)
def test_two_swaps_cancel_out(record):
    """Swapping twice returns every identifier to its element."""
    swap = IdMap(moved={"a": "b", "b": "a"})
    chained = composed(swap, swap)
    record(chained.to_dict())

    assert (chained.translate("a"), chained.translate("b")) == ("a", "b")
    assert chained.to_dict() == {"moved": {}, "removed": []}


@case(
    id="U-50",
    feature="Id map / composed: move, then remove",
    level="unit",
    priority="High",
    why="An element that moved and was then deleted is gone. Its links are broken, whatever name it had in between.",
    input="v2 moves s1 to s2; v3 removes s2",
    expected="v1's s1 translates to nothing",
)
def test_move_then_remove(record):
    """An element removed after moving is removed."""
    chained = composed(IdMap(moved={"s1": "s2"}), IdMap(removed={"s2"}))
    record(chained.to_dict())

    assert chained.translate("s1") is None


@case(
    id="U-51",
    feature="Id map / composed: removed, then another element moved onto the freed name",
    level="unit",
    priority="High",
    why="A freed identifier is soon taken by a neighbour. The removed element must stay removed, and the neighbour must not inherit its links.",
    input="v2 removes x; v3 moves y onto x",
    expected="x translates to nothing; y translates to x",
)
def test_freed_name_taken_by_another_element(record):
    """The element that takes a freed name is not the one that had it."""
    chained = composed(IdMap(removed={"x"}), IdMap(moved={"y": "x"}))
    record(chained.to_dict())

    assert chained.translate("x") is None
    assert chained.translate("y") == "x"


@case(
    id="U-52",
    feature="Id map / composed: three versions",
    level="unit",
    priority="High",
    why="A report across several versions chains every map in between. The result must not depend on how the chain is grouped.",
    input="v2 inserts at s1 (s1->s2, s2->s3); v3 removes s3; v4 inserts at s0 (s0->s1, s1->s2, s2->s3). Composed left to right, and right to left",
    expected="s0->s1, s1->s3, s2 gone; both groupings give the same map",
)
def test_three_versions_chain(record):
    """Chaining three maps gives one answer, whichever pair is joined first."""
    second = IdMap(moved={"s1": "s2", "s2": "s3"})
    third = IdMap(removed={"s3"})
    fourth = IdMap(moved={"s0": "s1", "s1": "s2", "s2": "s3"})
    left = second.then(third).then(fourth)
    right = second.then(third.then(fourth))
    record(f"left: {left.to_dict()}; right: {right.to_dict()}")

    assert {name: left.translate(name) for name in ("s0", "s1", "s2")} == {"s0": "s1", "s1": "s3", "s2": None}
    assert {name: right.translate(name) for name in ("s0", "s1", "s2")} == {"s0": "s1", "s1": "s3", "s2": None}



@case(
    id="U-53",
    feature="Net change / moved elements",
    level="unit",
    priority="High",
    why="A sentence that slid down because another was inserted is unchanged. Listing every one of them would bury the change that caused it; a sentence that went to another file is a real change.",
    input="UC1.txt: login, logout, borrow becomes: a new sentence, login, borrow - so login slides down one place. The logout sentence moved to UC2.txt",
    expected="Counts: 1 added, 2 moved. Listed: the added sentence, and the move to UC2.txt (old -> new); login, which only slid down, is not listed",
)
def test_moves_inside_a_file_are_counted_not_listed(record):
    """Elements that only changed position in their file are counted; a move to another file is listed."""
    before = items(SentencePreprocessor(), {"UC1.txt": f"{LOGIN} {LOGOUT} {BORROW}", "UC2.txt": EXTRA})
    after = items(SentencePreprocessor(), {"UC1.txt": f"{EXTRA} {LOGIN} {BORROW}", "UC2.txt": f"{EXTRA} {LOGOUT}"})
    changes = diff_elements(before, after)
    files = diff_files({"UC1.txt": "a", "UC2.txt": "b"}, {"UC1.txt": "c", "UC2.txt": "d"})
    summary = net_summary(SideChanges(role="source", files=files, elements=changes))
    listed = {row["path"]: [(e["change"], e["old"], e["new"]) for e in row["elements"]] for row in summary["files"]}
    record(f"counts: {summary['counts']}")
    record(f"listed: {listed}")

    assert summary["counts"]["elements_moved"] == 2
    assert listed["UC1.txt"] == [("added", None, "UC1.txt::sentence_0")]
    assert listed["UC2.txt"] == [("moved", "UC1.txt::sentence_1", "UC2.txt::sentence_1")]


@case(
    id="U-54",
    feature="Net change / a renamed method",
    level="unit",
    priority="High",
    why="A method renamed but otherwise kept is the same method. It must show as modified with its old and new name, and with a name a reader recognises.",
    input="Auth.java: login(String name) renamed to signIn(String name), body unchanged",
    expected="Listed once, as modified, old -> new: Auth.java::Auth::login(String name) -> Auth.java::Auth::signIn(String name), labelled 'Auth.signIn'",
)
def test_a_renamed_method_is_listed_old_to_new(record):
    """A renamed method is one modified element, listed with its old and new identifier."""
    body = "{ if (name == null) { throw new IllegalArgumentException(name); } session.open(name); audit.record(name); }"
    old = items(CodeMethodPreprocessor(), {"Auth.java": f"public class Auth {{ public void login(String name) {body} }}"}, "source code")
    new = items(CodeMethodPreprocessor(), {"Auth.java": f"public class Auth {{ public void signIn(String name) {body} }}"}, "source code")
    summary = net_summary(SideChanges(
        role="target", files=diff_files({"Auth.java": "a"}, {"Auth.java": "b"}), elements=diff_elements(old, new),
    ))
    listed = summary["files"][0]["elements"]
    record(listed)

    assert listed == [{
        "change": "modified", "old": "Auth.java::Auth::login(String name)",
        "new": "Auth.java::Auth::signIn(String name)", "label": "Auth.signIn",
    }]


@case(
    id="U-55",
    feature="Net change / a file renamed, then modified",
    level="unit",
    priority="High",
    why="Compared directly, a file renamed in one version and edited in a later one has a new path and new content, so nothing ties the two together. The renames the versions recorded have to.",
    input="v2 renames UC2.txt to archive/UC2.txt; v3 edits it; v4 renames it again to old/UC2.txt. The net file diff from v1 to v4, with and without the chained renames",
    expected="Chained renames: UC2.txt -> old/UC2.txt. With them, the file is renamed; without them, it reads as one removed and one added",
)
def test_a_file_renamed_then_modified(record):
    """Renames recorded along the way tie a renamed and later edited file back to its old path."""
    chained = chain_renames([{"UC2.txt": "archive/UC2.txt"}, {}, {"archive/UC2.txt": "old/UC2.txt"}])
    old, new = {"UC1.txt": "a", "UC2.txt": "b"}, {"UC1.txt": "a", "old/UC2.txt": "c"}
    with_renames, without = diff_files(old, new, chained), diff_files(old, new)
    record(f"chained renames: {chained}")
    record(f"with them: renamed {with_renames.renamed}; without: added {without.added}, removed {without.removed}")

    assert chained == {"UC2.txt": "old/UC2.txt"}
    assert with_renames.renamed == {"UC2.txt": "old/UC2.txt"} and not with_renames.added
    assert (without.added, without.removed) == (["old/UC2.txt"], ["UC2.txt"])


# Two methods with look-alike bodies, as generated service code has.
LOOKALIKE = """public class Manager {{
{listing}
    public boolean modify(Bean pBean) throws RemoteException {{
        if (!{checker}(pBean))
            throw new RemoteException(ErrorMessage.ERROR_DATA);
        try {{
            return (db.modify(pBean));
        }} catch (SQLException e) {{
            throw new RemoteException(ErrorMessage.ERROR_DBMS);
        }} catch (Exception e) {{
            throw new RemoteException(ErrorMessage.ERROR_UNKNOWN);
        }}
    }}
}}
"""
LISTING = """    public ArrayList<Bean> getAll() throws RemoteException {
        try {
            return (db.getList());
        } catch (SQLException e) {
            throw new RemoteException(ErrorMessage.ERROR_DBMS);
        } catch (Exception e) {
            throw new RemoteException(ErrorMessage.ERROR_UNKNOWN);
        }
    }
"""


@case(
    id="U-59",
    feature="Element diff / a method removed beside one that was edited",
    level="unit",
    priority="High",
    why="When one method is removed and its neighbour is edited in the same commit, the edited one must stay itself. Pairing the removed method with it reports the wrong method as removed and sends its pinned links to the wrong place.",
    input="A class with getAll() and modify(Bean), whose bodies look alike. New version: getAll() removed, and one call inside modify(Bean) renamed",
    expected="getAll() removed; modify(Bean) modified and still mapped to itself; nothing added",
)
def test_edited_method_is_not_paired_with_a_removed_lookalike(record):
    """An edited method keeps its identity when a similar method next to it is removed."""
    old = items(CodeMethodPreprocessor(), {"Manager.java": LOOKALIKE.format(listing=LISTING, checker="Checker.check")}, "source code")
    new = items(CodeMethodPreprocessor(), {"Manager.java": LOOKALIKE.format(listing="", checker="Validator.verify")}, "source code")
    changes = diff_elements(old, new)
    record(f"removed {changes.removed}; modified {changes.modified}; added {changes.added}")
    record(f"modify(Bean) maps to: {changes.id_map.translate('Manager.java::Manager::modify(Bean pBean)')}")

    assert changes.removed == ["Manager.java::Manager::getAll()"]
    assert changes.modified == [("Manager.java::Manager::modify(Bean pBean)", "Manager.java::Manager::modify(Bean pBean)")]
    assert changes.added == []
    assert changes.id_map.translate("Manager.java::Manager::modify(Bean pBean)") == "Manager.java::Manager::modify(Bean pBean)"
    assert changes.id_map.translate("Manager.java::Manager::getAll()") is None
