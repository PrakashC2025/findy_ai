"""Dependency-free text features: tokenising, hashing embeddings, similarity.

Local-first means we cannot assume a GPU or a 500 MB embedding download just to
remember a preference.  The default embedder is a deterministic feature-hashing
model over word, bigram and character-trigram features, L2-normalised.  It is
good at short-text recall (preferences, symbol names, error strings) and costs
microseconds per document.

When a real embedding model (``nomic-embed-text`` / ``bge-m3``) is installed,
:func:`devmind.memory.store.MemoryStore.set_embedder` can swap in a callable of
the same shape without touching any caller.
"""

from __future__ import annotations

import hashlib
import math
import re
from array import array
from collections import Counter
from itertools import pairwise

#: 1024 keeps hash collisions (and therefore unrelated-text similarity) near zero.
DIM = 1024

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "is",
    "it",
    "for",
    "on",
    "with",
    "as",
    "at",
    "by",
    "be",
    "this",
    "that",
    "from",
    "are",
    "was",
    "i",
    "you",
    "my",
    "me",
    "we",
    "do",
    "does",
    "how",
    "what",
    "why",
    "can",
}


def tokenize(text: str) -> list[str]:
    """Lowercase, split camelCase/snake_case, drop stop-words."""
    out: list[str] = []
    for raw in _WORD_RE.findall(text or ""):
        out.extend(part.lower() for part in _CAMEL_RE.split(raw))
    return [token for token in out if token and token not in _STOP]


def _hash(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8", "ignore"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % DIM, 1.0 if (value >> 63) & 1 else -1.0


def embed(text: str) -> array:
    """Deterministic feature-hashing embedding, L2-normalised."""
    vector = [0.0] * DIM
    tokens = tokenize(text)
    if not tokens:
        return array("f", vector)

    counts: Counter[str] = Counter()
    for token in tokens:
        counts[token] += 1.0
        if len(token) > 3:
            for i in range(len(token) - 2):
                counts["#" + token[i : i + 3]] += 0.35
    for left, right in pairwise(tokens):
        counts[f"{left}_{right}"] += 0.6

    for feature, weight in counts.items():
        index, sign = _hash(feature)
        vector[index] += sign * math.log1p(weight)

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return array("f", (value / norm for value in vector))


def cosine(left: array, right: array) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def jaccard(left: str, right: str) -> float:
    first, second = set(tokenize(left)), set(tokenize(right))
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def similarity(left: str, right: str) -> float:
    """Blend of lexical overlap and embedding cosine, both in [0, 1]."""
    lexical = jaccard(left, right)
    semantic = max(0.0, cosine(embed(left), embed(right)))
    return 0.55 * lexical + 0.45 * semantic


def to_bytes(vector: array) -> bytes:
    return vector.tobytes()


def from_bytes(blob: bytes) -> array:
    vector = array("f")
    try:
        vector.frombytes(blob)
    except (ValueError, TypeError):
        return array("f", [0.0] * DIM)
    return vector
