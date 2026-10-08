"""One-sentence summaries of elements that are not written in prose (natural language).
"""

import logging
import re

from core.classification.chat_provider import ChatProvider
from core.schemas import Element

logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 8

MAX_CONTENT_CHARS = 1200
MAX_SUMMARY_CHARS = 200

SYSTEM_MESSAGE = (
    "You summarise software artifacts for a search index. "
    "For each numbered item, reply with one line: the item's number, a colon, "
    "then one sentence of at most 20 words saying what the item is for. "
    "Describe its purpose, not its syntax. "
    "Output exactly one line per item, numbered as given, and nothing else."
)

# "3: Stores the uploaded file." - the number, any separator, then the text.
SUMMARY_LINE = re.compile(r"^\s*(\d+)\s*[:.)\-]\s*(.+?)\s*$")


class ElementSummarizer:

    def __init__(
        self,
        provider: ChatProvider,
        cache=None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        self.provider = provider
        self.cache = cache
        self.batch_size = batch_size

    def summarize(self, elements: list[Element]) -> None:
        """Attach a summary to each element that will be compared, in place."""
        pending = [e for e in elements if e.compare and e.content.strip()]
        if not pending:
            return

        known = self.cache.get_many([e.content for e in pending]) if self.cache else {}

        unsummarised: dict[str, Element] = {}
        for element in pending:
            if element.content not in known:
                unsummarised.setdefault(element.content, element)

        missing = list(unsummarised.values())
        logger.info(
            f"Summarising {len(missing)} distinct elements of {len(pending)} compared "
            f"({len(pending) - len(missing)} already known)"
        )

        fresh: dict[str, str] = {}
        for start in range(0, len(missing), self.batch_size):
            fresh.update(self._summarize_batch(missing[start:start + self.batch_size]))

        if self.cache and fresh:
            self.cache.set_many(fresh)

        known.update(fresh)
        for element in pending:
            element.summary = known.get(element.content)

    def _summarize_batch(self, batch: list[Element]) -> dict[str, str]:
        prompt = "\n\n".join(
            f"{number}. [{element.type}] {element.identifier}\n{self._excerpt(element.content)}"
            for number, element in enumerate(batch, start=1)
        )

        try:
            reply = self.provider.chat(prompt, system_message=SYSTEM_MESSAGE)
        except Exception as error:
            logger.warning(f"Could not summarise {len(batch)} elements: {error}")
            return {}

        numbered = self._parse(reply)
        missed = len(batch) - sum(1 for number in numbered if 1 <= number <= len(batch))
        if missed:
            logger.warning(f"{missed} of {len(batch)} elements came back without a summary")

        return {
            element.content: numbered[number]
            for number, element in enumerate(batch, start=1)
            if number in numbered
        }

    @staticmethod
    def _parse(reply: str) -> dict[int, str]:
        """The numbered lines the model returned, ignoring anything else."""
        found = {}
        for line in (reply or "").splitlines():
            match = SUMMARY_LINE.match(line)
            if match:
                found[int(match.group(1))] = match.group(2)[:MAX_SUMMARY_CHARS]
        return found

    @staticmethod
    def _excerpt(content: str) -> str:
        stripped = content.strip()
        if len(stripped) <= MAX_CONTENT_CHARS:
            return stripped
        return f"{stripped[:MAX_CONTENT_CHARS]}…"
