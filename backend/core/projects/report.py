"""The change report between two versions of one analysis.
Every link from either run is given one state:

    valid            in both runs
    new              only in the later run
    no_longer_found  only in the earlier run, and both its elements still exist
    broken           only in the earlier run, and one of its elements is gone

and a reason: whether its source element changed, whether its target element
changed - or neither, in which case the files are not why the link moved; the
classifier, or a top-k that shifted, is.
"""

from dataclasses import dataclass, field

from core.projects.diff import IdMap, file_of

VALID = "valid"
NEW = "new"
NO_LONGER_FOUND = "no_longer_found"
BROKEN = "broken"


@dataclass
class RunView:
    """One run as the report reads it."""

    # (source id, target id) -> what the run said about the link.
    links: dict[tuple[str, str], dict]
    # Each element's identifier -> the hash of its content, at the level the
    # run reports links on.
    source_hashes: dict[str, str]
    target_hashes: dict[str, str]
    # Source elements the run linked to nothing.
    uncovered: list[str]
    # What elements are called where a person reads them, where that is not
    # their identifier: a UML component's name.
    names: dict[str, str] = field(default_factory=dict)


def change_report(
    base: RunView | None,
    head: RunView,
    source_map: IdMap,
    target_map: IdMap,
) -> dict:
    """Compare two runs of one analysis, link by link."""
    base = base or RunView(links=dict(head.links), source_hashes=dict(head.source_hashes),
                           target_hashes=dict(head.target_hashes), uncovered=[])

    # Which earlier element each later one was, so a link only the later run
    # found can say whether its ends are new or edited.
    was_source = _reverse(base.source_hashes, source_map)
    was_target = _reverse(base.target_hashes, target_map)

    def changed(identifier, before_hashes, now_hashes, was) -> bool:
        earlier = was.get(identifier)
        return earlier is None or before_hashes.get(earlier) != now_hashes.get(identifier)

    links = []
    matched = set()
    for (source, target), found in base.links.items():
        now_source, now_target = source_map.translate(source), target_map.translate(target)
        source_present = now_source in head.source_hashes
        target_present = now_target in head.target_hashes

        if (now_source, now_target) in head.links:
            state = VALID
            matched.add((now_source, now_target))
            found = head.links[(now_source, now_target)]
        elif source_present and target_present:
            state = NO_LONGER_FOUND
        else:
            state = BROKEN

        links.append({
            "state": state,
            # Named as the later run names them, where the element still exists.
            "source_id": now_source if source_present else source,
            "target_id": now_target if target_present else target,
            "source_present": source_present,
            "target_present": target_present,
            "source_changed": (
                not source_present
                or base.source_hashes.get(source) != head.source_hashes.get(now_source)
            ),
            "target_changed": (
                not target_present
                or base.target_hashes.get(target) != head.target_hashes.get(now_target)
            ),
            **found,
        })

    for (source, target), found in head.links.items():
        if (source, target) in matched:
            continue
        links.append({
            "state": NEW,
            "source_id": source,
            "target_id": target,
            "source_present": True,
            "target_present": True,
            "source_changed": changed(source, base.source_hashes, head.source_hashes, was_source),
            "target_changed": changed(target, base.target_hashes, head.target_hashes, was_target),
            **found,
        })

    # Only elements the later run has: a requirement that was deleted is not
    # a gap in coverage, it is simply gone.
    uncovered = sorted(name for name in head.uncovered if name in head.source_hashes)
    counts = {state: sum(1 for link in links if link["state"] == state)
              for state in (VALID, NO_LONGER_FOUND, BROKEN, NEW)}
    return {
        "links": links,
        "uncovered": uncovered,
        "summary": {**counts, "uncovered": len(uncovered)},
    }


def _reverse(hashes: dict[str, str], id_map: IdMap) -> dict[str, str]:
    """Later identifier -> the earlier identifier it was."""
    reverse = {}
    for identifier in hashes:
        now = id_map.translate(identifier)
        if now is not None:
            reverse[now] = identifier
    return reverse


def count_file_links(rows: list[dict], links: list[dict], role: str) -> None:
    """How many of a report's links touch each changed file of one side."""
    end = "source" if role == "source" else "target"
    by_path = {}
    for row in rows:
        row.update(links=0, changed=0)
        for path in (row["path"], row["old_path"]):
            if path:
                by_path[path] = row
    for link in links:
        row = by_path.get(file_of(link[f"{end}_id"]))
        if row is not None:
            row["links"] += 1
            row["changed"] += link[f"{end}_changed"]
