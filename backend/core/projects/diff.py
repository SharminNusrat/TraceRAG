"""What changed between two file sets of one side, and where each element went."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256

from core.output.names import display_names, file_of, model_name
from core.schemas import Element

# How alike two texts must be for an edited element to count as the old one
# reworded, rather than as one removed and an unrelated one added.
SIMILAR = 0.6

# Matching edited elements by similarity compares every old one with every new
# one in a changed stretch. Past this many pairs the stretch is a rewrite, and
# only identifiers are used to pair what is left.
MAX_SIMILARITY_PAIRS = 2500


def element_hash(text: str) -> str:
    """A name for an element's content that ignores how it is laid out."""
    return sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Item:
    """One element as the diff reads it."""

    identifier: str
    # The file it lives in: the identifier of its top-most ancestor.
    file: str
    level: str
    compare: bool
    content: str
    # A UML component's name from its model; None for anything else.
    name: str | None = None

    @property
    def hash(self) -> str:
        return element_hash(self.content)


def items_from_elements(elements: list[Element]) -> list[Item]:
    """A preprocessor's elements, each tagged with the file it belongs to."""
    parents = {element.identifier: element.parent_id for element in elements}

    def root(identifier: str) -> str:
        seen = set()
        while parents.get(identifier) and identifier not in seen:
            seen.add(identifier)
            identifier = parents[identifier]
        return identifier

    return [
        Item(
            identifier=element.identifier,
            file=root(element.identifier),
            level=element.level.value,
            compare=element.compare,
            content=element.content,
            name=model_name(element.level.value, element.model_units),
        )
        for element in elements
    ]


# ----- Files -----

@dataclass
class FileChanges:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    # Old path -> new path.
    renamed: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.renamed)

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "renamed": [{"old": old, "new": new} for old, new in sorted(self.renamed.items())],
        }


def diff_files(
    old: dict[str, str],
    new: dict[str, str],
    renames: dict[str, str] | None = None,
) -> FileChanges:
    """Compare two file sets, each given as relative path -> content hash."""
    gone = sorted(set(old) - set(new))
    arrived = sorted(set(new) - set(old))
    renamed: dict[str, str] = {}

    # The origin's word first: it also covers a file renamed and edited.
    for before, after in (renames or {}).items():
        if before in gone and after in arrived:
            renamed[before] = after

    by_hash: dict[str, list[str]] = {}
    for path in arrived:
        if path not in renamed.values():
            by_hash.setdefault(new[path], []).append(path)
    for path in gone:
        if path in renamed:
            continue
        candidates = by_hash.get(old[path], [])
        if len(candidates) == 1:
            renamed[path] = candidates.pop()

    return FileChanges(
        added=[path for path in arrived if path not in renamed.values()],
        removed=[path for path in gone if path not in renamed],
        modified=sorted(path for path in set(old) & set(new) if old[path] != new[path]),
        renamed=renamed,
    )


# ----- The id map -----

@dataclass
class IdMap:
    """What each old identifier is called now."""

    moved: dict[str, str] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)

    def translate(self, identifier: str) -> str | None:
        if identifier in self.removed:
            return None
        return self.moved.get(identifier, identifier)

    def then(self, later: "IdMap") -> "IdMap":
        """This map followed by a later one, as a single map."""
        moved: dict[str, str] = {}
        removed = set(self.removed)
        for old, new in self.moved.items():
            final = later.translate(new)
            if final is None:
                removed.add(old)
            elif final != old:
                moved[old] = final
        # The later map reads identifiers as they are after this one. Only a
        # name this map left alone still means the same element there; a name
        # it moved or removed was followed above, and one it moved something
        # onto now means that other element.
        def left_alone(name: str) -> bool:
            return (
                name not in self.moved and name not in self.removed
                and name not in self.moved.values()
            )

        for old, new in later.moved.items():
            if left_alone(old):
                moved[old] = new
        for old in later.removed:
            if left_alone(old):
                removed.add(old)
        return IdMap(moved=moved, removed=removed)

    def to_dict(self) -> dict:
        return {"moved": dict(sorted(self.moved.items())), "removed": sorted(self.removed)}

    @classmethod
    def from_dict(cls, data: dict | None) -> "IdMap":
        data = data or {}
        return cls(moved=dict(data.get("moved", {})), removed=set(data.get("removed", [])))


def translate_pins(
    pins: dict[str, set[str]], source: IdMap, target: IdMap
) -> dict[str, set[str]]:
    """Last run's pinned pairs, under the identifiers the new files use."""
    translated: dict[str, set[str]] = {}
    for old_source, targets in pins.items():
        new_source = source.translate(old_source)
        if new_source is None:
            continue
        for old_target in targets:
            new_target = target.translate(old_target)
            if new_target is not None:
                translated.setdefault(new_source, set()).add(new_target)
    return translated


# ----- Elements -----

@dataclass
class ElementChanges:
    """How the compared elements changed, and the id map for every level."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # (old identifier, new identifier); the two are often the same.
    modified: list[tuple[str, str]] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    id_map: IdMap = field(default_factory=IdMap)
    # A name a reader recognises for each compared element above, old and new.
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def meaningful(self) -> bool:
        """Whether anything the classifier judges is different."""
        return bool(self.added or self.removed or self.modified or self.moved)

    def summary(self) -> dict:
        """The changes as a user is shown them."""
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": [new for _, new in self.modified],
            "moved": [{"old": old, "new": new} for old, new in self.moved],
        }


# How many words of a text element stand for it in a list.
LABEL_WORDS = 8


def label_of(item: Item) -> str:
    """A name for an element a reader recognises, rather than its identifier."""
    if item.level in ("function", "class"):
        member = item.identifier.partition("::")[2] or item.identifier
        return member.split("(")[0].replace("::", ".")
    words = item.content.split()
    return " ".join(words[:LABEL_WORDS]) + (" …" if len(words) > LABEL_WORDS else "")


def chain_renames(steps: list[dict[str, str]]) -> dict[str, str]:
    """Several versions' file renames, as one map from the first name to the last."""
    chained: dict[str, str] = {}
    for step in steps:
        # A file renamed again: follow it from its first name.
        followed = set()
        for old, new in list(chained.items()):
            if new in step:
                chained[old] = step[new]
                followed.add(new)
        for old, new in step.items():
            if old not in followed:
                chained[old] = new
    return {old: new for old, new in chained.items() if old != new}


def _ratio(old: Item, new: Item) -> float:
    matcher = SequenceMatcher(None, old.content, new.content, autojunk=False)
    # The cheap upper bounds first: most unrelated pairs stop here.
    if matcher.real_quick_ratio() < SIMILAR or matcher.quick_ratio() < SIMILAR:
        return 0.0
    return matcher.ratio()


def _pair_changed(olds: list[Item], news: list[Item], rename) -> tuple[list, list, list]:
    """Pair the elements of a stretch that changed between two versions."""
    pairs: list[tuple[Item, Item]] = []
    free = list(news)

    if len(olds) * len(news) <= MAX_SIMILARITY_PAIRS:
        # The closest matches first. Taken in file order instead, a removed
        # element claims a look-alike neighbour that was only edited, and the
        # edited one is then reported as the one removed.
        scored = sorted(
            ((_ratio(old, new), o, n)
             for o, old in enumerate(olds) if old.compare
             for n, new in enumerate(news)),
            key=lambda entry: (-entry[0], entry[1], entry[2]),
        )
        matched: dict[int, int] = {}
        for score, o, n in scored:
            if score < SIMILAR:
                break
            if o not in matched and n not in matched.values():
                matched[o] = n
        pairs = [(olds[o], news[n]) for o, n in sorted(matched.items())]
        free = [new for n, new in enumerate(news) if n not in matched.values()]

    paired = {old.identifier for old, _ in pairs}
    for old in olds:
        if old.identifier in paired:
            continue
        same = next((new for new in free if new.identifier == rename(old.identifier)), None)
        if same is not None:
            pairs.append((old, same))
            paired.add(old.identifier)
            free.remove(same)

    removed = [old for old in olds if old.identifier not in paired]
    return pairs, removed, free


def diff_elements(
    old: list[Item],
    new: list[Item],
    renames: dict[str, str] | None = None,
) -> ElementChanges:
    """Compare the elements of two file sets of one side."""
    renames = renames or {}

    def rename(identifier: str) -> str:
        # The same identifier under the file's new path, for pairing a
        # renamed file's elements that kept their names.
        path, separator, member = identifier.partition("::")
        if path in renames:
            return f"{renames[path]}{separator}{member}"
        for before, after in renames.items():
            if identifier.startswith(f"{before}$"):
                return after + identifier[len(before):]
        return identifier

    def grouped(items: list[Item]) -> dict[tuple[str, str], list[Item]]:
        groups: dict[tuple[str, str], list[Item]] = {}
        for item in items:
            groups.setdefault((item.file, item.level), []).append(item)
        return groups

    old_groups = grouped(old)
    new_groups = grouped(new)
    keys = list(dict.fromkeys(
        [(renames.get(file, file), level) for file, level in old_groups] + list(new_groups)
    ))
    old_by_new_key = {(renames.get(file, file), level): items for (file, level), items in old_groups.items()}

    pairs: list[tuple[Item, Item]] = []
    removed: list[Item] = []
    added: list[Item] = []

    for key in keys:
        before = old_by_new_key.get(key, [])
        after = new_groups.get(key, [])
        matcher = SequenceMatcher(
            None, [item.hash for item in before], [item.hash for item in after], autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                pairs.extend(zip(before[i1:i2], after[j1:j2]))
            elif tag == "delete":
                removed.extend(before[i1:i2])
            elif tag == "insert":
                added.extend(after[j1:j2])
            else:
                matched, lost, found = _pair_changed(before[i1:i2], after[j1:j2], rename)
                pairs.extend(matched)
                removed.extend(lost)
                added.extend(found)

    # An element removed in one place and added unchanged in another moved -
    # two swapped sentences are exactly this.
    arrived: dict[str, list[Item]] = {}
    for item in added:
        arrived.setdefault(item.hash, []).append(item)
    still_removed = []
    for item in removed:
        same = arrived.get(item.hash)
        if same:
            match = same.pop(0)
            pairs.append((item, match))
            added.remove(match)
        else:
            still_removed.append(item)
    removed = still_removed

    id_map = IdMap(
        moved={old.identifier: new.identifier for old, new in pairs if old.identifier != new.identifier},
        removed={item.identifier for item in removed},
    )
    compared = [(old, new) for old, new in pairs if new.compare]
    involved = [item for pair in compared for item in pair] + added + removed
    # A component is labelled by its name, the same way everywhere else shows it.
    names = display_names([(item.identifier, item.name) for item in old + new if item.name])
    return ElementChanges(
        added=sorted(item.identifier for item in added if item.compare),
        removed=sorted(item.identifier for item in removed if item.compare),
        modified=sorted(
            (old.identifier, new.identifier) for old, new in compared if old.hash != new.hash
        ),
        moved=sorted(
            (old.identifier, new.identifier) for old, new in compared
            if old.hash == new.hash and old.identifier != new.identifier
        ),
        id_map=id_map,
        labels={
            item.identifier: names.get(item.identifier) or label_of(item)
            for item in involved if item.compare
        },
    )


@dataclass
class SideChanges:
    """Everything that changed on one side between two versions."""

    role: str
    files: FileChanges
    elements: ElementChanges

    def to_dict(self) -> dict:
        """As it is stored on the version: the summary, and the map behind it."""
        return {
            "role": self.role,
            "files": self.files.to_dict(),
            "elements": self.elements.summary(),
            "id_map": self.elements.id_map.to_dict(),
        }


def net_summary(changes: SideChanges) -> dict:
    """One side's net change between two versions, grouped by file."""
    files, elements = changes.files, changes.elements
    renamed = files.renamed
    rows = {path: {"path": path, "old_path": None, "change": change, "elements": []}
            for change, paths in (("added", files.added), ("removed", files.removed),
                                  ("modified", files.modified)) for path in paths}
    for old, new in renamed.items():
        rows[new] = {"path": new, "old_path": old, "change": "renamed", "elements": []}

    def row_for(identifier: str) -> dict | None:
        path = file_of(identifier)
        return rows.get(renamed.get(path, path))

    def place(change: str, old: str | None, new: str | None) -> None:
        row = row_for(new or old)
        if row is not None:
            row["elements"].append({
                "change": change, "old": old, "new": new,
                "label": elements.labels.get(new or old) or elements.labels.get(old) or (new or old),
            })

    for identifier in elements.added:
        place("added", None, identifier)
    for identifier in elements.removed:
        place("removed", identifier, None)
    for old, new in elements.modified:
        place("modified", old, new)
    for old, new in elements.moved:
        if renamed.get(file_of(old), file_of(old)) != file_of(new):
            place("moved", old, new)

    return {
        "role": changes.role,
        "counts": {
            "files_added": len(files.added),
            "files_removed": len(files.removed),
            "files_modified": len(files.modified),
            "files_renamed": len(renamed),
            "elements_added": len(elements.added),
            "elements_removed": len(elements.removed),
            "elements_modified": len(elements.modified),
            "elements_moved": len(elements.moved),
        },
        "files": sorted(rows.values(), key=lambda row: row["path"]),
    }
