"""The settings a run actually uses, as opposed to the ones it was given."""


def expansion_depth(target_kind: str | None, depth: int) -> int:
    """The dependency expansion a run actually performs."""
    # Unknown kind: a run saved without its files. Left as it was given.
    if target_kind is None or target_kind == "code":
        return depth
    return 0
