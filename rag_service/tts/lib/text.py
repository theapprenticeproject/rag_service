import hashlib
import re
import unicodedata
from collections import namedtuple

_SENTENCE_END_RE = re.compile(r"[.!?\u0964]")

PreparedText = namedtuple("PreparedText", "text language fingerprint truncated")


def clean_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text or "")
    out = []
    for ch in normalized:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn") and ch not in ("\n", "\t"):
            continue
        out.append(ch)
    collapsed = "".join(out).replace("\t", " ").replace("\n", " ")
    return " ".join(collapsed.split())


def _truncate(text: str, limit: int):
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    matches = list(_SENTENCE_END_RE.finditer(window))
    if matches:
        return window[: matches[-1].end()].strip(), True
    last_space = window.rfind(" ")
    if last_space > 0:
        return window[:last_space].strip(), True
    return "", True


def prepare(raw_text: str, max_chars: int, policy: str):
    cleaned = clean_text(raw_text or "")
    if len(cleaned) <= max_chars:
        return cleaned, False
    if policy == "skip":
        return "", True
    truncated, was_truncated = _truncate(cleaned, max_chars)
    return clean_text(truncated), was_truncated


def fingerprint(text: str, language: str) -> str:
    payload = f"{language}\x1f{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prepare_item(raw_text, raw_language, max_chars, policy):
    from . import languages

    language = languages.normalize(raw_language)
    if language is None:
        return None
    cleaned, truncated = prepare(raw_text or "", max_chars, policy)
    if not cleaned:
        return None
    return PreparedText(cleaned, language, fingerprint(cleaned, language), truncated)
