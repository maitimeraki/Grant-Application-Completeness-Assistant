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


def score_blocks(query: str, blocks: list, top_k: int = 8) -> list[tuple[float, object]]:
    """Lightweight BM25-style ranking of text blocks against a query."""
    if not blocks:
        return []
    tokenized = [Counter(tokenize(b.text)) for b in blocks]
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
