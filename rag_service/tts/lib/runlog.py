import re

import frappe

from ..constants import LOG_CAP_BYTES, RUN_DOCTYPE

TRUNCATION_MARKER = "...[log truncated]...\n"
TOKEN_LIKE = re.compile(r"\b[0-9]{9,}\.[0-9a-f]{32,}\b")
URL_LIKE = re.compile(r"https?://\S+")


def _redact(line):
    line = TOKEN_LIKE.sub("[token]", line)
    line = URL_LIKE.sub("[url]", line)
    return line


def append(run_name, message):
    line = f"{frappe.utils.now()} {_redact(str(message))}"
    current = frappe.db.get_value(RUN_DOCTYPE, run_name, "run_log") or ""
    updated = current + line + "\n"
    if len(updated.encode("utf-8")) > LOG_CAP_BYTES:
        keep_bytes = LOG_CAP_BYTES - len(TRUNCATION_MARKER.encode("utf-8"))
        encoded = updated.encode("utf-8")[-keep_bytes:]
        updated = TRUNCATION_MARKER + encoded.decode("utf-8", errors="ignore")
    frappe.db.set_value(RUN_DOCTYPE, run_name, "run_log", updated, update_modified=False)
