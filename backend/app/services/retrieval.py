from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "are",
    "was", "were", "be", "been", "by", "with", "as", "at", "it", "its", "this",
    "that", "these", "those", "must", "should", "shall", "will", "can", "may",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").casefold()) if t not in STOPWORDS]


def tokenize_blocks(blocks: list) -> list[Counter]:
    """Pre-tokenize blocks once so score_blocks can be called per-requirement
    without re-tokenizing the whole document every time."""
    return [Counter(tokenize(b.text)) for b in blocks]


def score_blocks(
    query: str,
    blocks: list,
    top_k: int = 8,
    *,
    tokenized: list[Counter] | None = None,
) -> list[tuple[float, object]]:
    """Lightweight BM25-style ranking of text blocks against a query.

    Pass `tokenized=tokenize_blocks(blocks)` when scoring many queries against
    the same blocks to avoid repeated tokenization.
    """
    if not blocks:
        return []
    if tokenized is None:
        tokenized = tokenize_blocks(blocks)
    n = len(blocks)
    doc_freq: Counter[str] = Counter()
    for tf in tokenized:
        doc_freq.update(tf.keys())

    query_tokens = tokenize(query)
    scored: list[tuple[float, object]] = []
    for block, tf in zip(blocks, tokenized):
        score = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if not f:
                continue
            df = doc_freq[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += idf * (f * 2.2) / (f + 1.2)
        if score > 0:
            scored.append((score, block))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:top_k]
