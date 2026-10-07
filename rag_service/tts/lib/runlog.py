import re

import frappe

from ..constants import LOG_CAP_BYTES, RUN_DOCTYPE

TRUNCATION_MARKER = "...[log truncated]...\n"
TOKEN_LIKE = re.compile(r"\b\d{6,}\.[0-9a-fA-F]{16,}\b")
URL_LIKE = re.compile(r"https?://\S+")
HEADER_LIKE = re.compile(r"(?i)(x-run-token|authorization)\s*[:=]\s*\S+")


def _redact(line):
    line = HEADER_LIKE.sub(lambda m: m.group(0).split(m.group(1), 1)[0] + m.group(1) + ": [redacted]", line)
    line = TOKEN_LIKE.sub("[token]", line)
    line = URL_LIKE.sub("[url]", line)
    return line


def append(run_name, message):
    line = f"{frappe.utils.now()} {_redact(str(message))}\n"
    frappe.db.sql(
        f"""
        UPDATE `tab{RUN_DOCTYPE}`
        SET run_log = CONCAT(COALESCE(run_log, ''), %(line)s)
        WHERE name = %(name)s
        """,
        {"name": run_name, "line": line},
    )
    current_len = frappe.db.sql(
        f"SELECT LENGTH(run_log) FROM `tab{RUN_DOCTYPE}` WHERE name = %(name)s",
        {"name": run_name},
    )
    length = current_len[0][0] if current_len and current_len[0][0] is not None else 0
    if length > LOG_CAP_BYTES:
        keep_bytes = LOG_CAP_BYTES - len(TRUNCATION_MARKER.encode("utf-8"))
        current = frappe.db.get_value(RUN_DOCTYPE, run_name, "run_log") or ""
        encoded = current.encode("utf-8")[-keep_bytes:]
        updated = TRUNCATION_MARKER + encoded.decode("utf-8", errors="ignore")
        frappe.db.set_value(RUN_DOCTYPE, run_name, "run_log", updated, update_modified=False)