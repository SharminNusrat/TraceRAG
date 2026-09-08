"""When two pieces of text count as the same thing.

Both the classifier's cache and the graph ask this question, and they have to
answer it identically: if the cache says a method is unchanged but the graph
says it moved, one of them re-buys work the other already paid for.
"""

import os
from hashlib import sha256


def relative_identifier(identifier: str, roots) -> str:
    """An element identifier reduced to the form it is stored in.

    Providers do not agree on how they name things: the code one uses the
    absolute path on disk, the document one the path within the workspace. That
    is invisible until something has to match an identifier from one run
    against one from another - a pinned link, above all - because a comparison
    between the two forms does not fail loudly, it just never matches.

    Only the part before "::" is a path. What follows names something inside the
    file and is carried across untouched.
    """
    path, separator, member = identifier.partition("::")
    for root in roots:
        prefix = f"{root}{os.sep}"
        if path.startswith(prefix):
            # Separators are only rewritten on a path that was actually inside
            # the workspace. One that was not is left exactly as it came, so
            # this cannot quietly alter an identifier it does not understand.
            path = path[len(prefix):].replace(os.sep, "/")
            break
    return f"{path}{separator}{member}"


def normalise(text: str) -> str:
    """Drop the whitespace a reader would not have noticed.

    Reformatting a file rewrites every line without changing what any of it
    means. Left alone, one `black .` commit would look like a whole new
    codebase - a full re-embed, a full re-classification, and every link
    reported as both removed and added.
    """
    lines = (line.rstrip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def content_hash(text: str) -> str:
    """A short name for this content, ignoring formatting."""
    return sha256(normalise(text).encode("utf-8")).hexdigest()
