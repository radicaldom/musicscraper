"""Name normalisation and fuzzy matching shared by the library, verifier and matcher."""

import re
import unicodedata
from difflib import SequenceMatcher

_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_NON_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def norm(s: str) -> str:
    """Lowercase, strip accents/punctuation/bracketed suffixes and a leading 'the'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = _BRACKETS.sub(" ", s).replace("&", " and ").replace("_", " ")
    s = _SPACES.sub(" ", _NON_WORD.sub(" ", s)).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


def tokens(s: str) -> list[str]:
    return norm(s).split()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def token_coverage(needle: str, haystack: str) -> float:
    """Fraction of needle's tokens that appear in haystack's tokens."""
    want = tokens(needle)
    if not want:
        return 0.0
    have = set(tokens(haystack))
    return sum(1 for t in want if t in have) / len(want)


def search_query(*parts: str) -> str:
    """Soulseek-friendly query: plain words, no punctuation or bracketed editions."""
    words = []
    for part in parts:
        words.extend(_SPACES.sub(" ", _NON_WORD.sub(" ", _BRACKETS.sub(" ", part))).split())
    return " ".join(words)
