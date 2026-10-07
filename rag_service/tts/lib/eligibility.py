import json

import frappe

from ..constants import FEEDBACK_REQUEST_DOCTYPE
from .text import fingerprint as fingerprint_text
from .text import prepare as prepare_text
from . import languages


class EligibleItem:
    def __init__(self, id, text, language):
        self.id = id
        self.text = text
        self.language = language


class EligibilityResult:
    def __init__(self, items, skipped_unsupported_language, skipped_empty_text, skipped_stale_fingerprint):
        self.items = items
        self.skipped_unsupported_language = skipped_unsupported_language
        self.skipped_empty_text = skipped_empty_text
        self.skipped_stale_fingerprint = skipped_stale_fingerprint


def find_eligible(settings, max_scan, only_names=None):
    filters = [["audio_status", "=", "Queued"]]
    if only_names is not None:
        filters.append(["name", "in", list(only_names)])

    rows = frappe.get_all(
        FEEDBACK_REQUEST_DOCTYPE,
        filters=filters,
        fields=["name", "creation", "generated_feedback", "audio_source_text", "audio_text_fingerprint"],
        order_by="creation asc",
        limit_page_length=max_scan,
    )

    items = []
    skipped_unsupported_language = 0
    skipped_empty_text = 0
    skipped_stale_fingerprint = 0

    for row in rows:
        try:
            feedback = json.loads(row.generated_feedback or "{}")
        except Exception:
            feedback = {}

        language = languages.normalize(feedback.get("translation_language") or "English")
        if language is None:
            skipped_unsupported_language += 1
            continue

        source_text = (row.audio_source_text or "").strip()
        if not source_text:
            skipped_empty_text += 1
            continue

        prepared, _truncated = prepare_text(source_text, settings.max_text_chars(), settings.over_length_policy)
        if not prepared:
            skipped_empty_text += 1
            continue

        if fingerprint_text(prepared, language) != row.audio_text_fingerprint:
            skipped_stale_fingerprint += 1
            continue

        items.append(EligibleItem(id=row.name, text=prepared, language=language))

    return EligibilityResult(items, skipped_unsupported_language, skipped_empty_text, skipped_stale_fingerprint)