from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return _WHITESPACE.sub(" ", text).strip().casefold()


def quote_in_text(quote: str, text: str) -> bool:
    nq = normalize(quote)
    nt = normalize(text)
    if not nq:
        return False
    if nq in nt:
        return True
    # tolerate quotes that span slightly more/less punctuation
    nq_stripped = nq.strip(".,;:()[]\"'")
    return bool(nq_stripped) and nq_stripped in nt


def find_block_for_quote(quote: str, blocks) -> object | None:
    """Return the first block whose text contains the quote, else None.

    `blocks` is any iterable of objects with `.text` and `.id`.
    """
    best = None
    for block in blocks:
        if quote_in_text(quote, block.text):
            if best is None or len(block.text) < len(best.text):
                best = block
    return best
