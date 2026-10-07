import frappe

from ..constants import RUN_DOCTYPE, TERMINAL_STATUSES

TARGET_DOCTYPE = "Feedback Request"

TRANSITIONS = {
    "Draft": {"Deploying"},
    "Deploying": {"Starting", "Deploy Failed", "Skipped"},
    "Starting": {"Running", "Timed Out", "Deploy Failed", "Stopping"},
    "Running": {"Stopping", "Completed", "Completed With Failures", "Failed", "Timed Out", "Lost"},
    "Stopping": {"Stopped", "Timed Out"},
}

INT_FIELDS = frozenset({
    "eligible_found", "manifest_count", "served_count", "written", "already_set", "stale",
    "hash_mismatch", "not_in_manifest", "changed_during_write", "pod_failed", "not_processed",
    "pod_uploaded", "pod_generated", "pod_decoded", "pod_truncated", "pod_retried", "pod_tokens",
    "termination_attempts",
})

FLOAT_FIELDS = frozenset({
    "pod_audio_seconds", "pod_startup_seconds", "pod_run_seconds", "pod_tokens_per_second",
    "pod_gpu_sec_per_audio_min", "billed_seconds", "cost_per_clip_usd",
})

CURRENCY_FIELDS = frozenset({
    "estimated_cost_usd", "hourly_rate", "estimated_cost_usd_final", "cost_per_1000_usd",
})


def can_transition(from_status, to_status) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


def is_terminal(status) -> bool:
    return status in TERMINAL_STATUSES


def _sanitize(fields):
    clean = dict(fields)
    for key in INT_FIELDS:
        if key in clean and clean[key] is None:
            clean[key] = 0
    for key in FLOAT_FIELDS:
        if key in clean and clean[key] is None:
            clean[key] = 0.0
    for key in CURRENCY_FIELDS:
        if key in clean and clean[key] is None:
            clean[key] = 0
    return clean


def set_fields(run_name, fields: dict) -> None:
    frappe.db.set_value(RUN_DOCTYPE, run_name, _sanitize(fields), update_modified=False)


def increment(run_name, field, amount=1) -> None:
    current = frappe.db.get_value(RUN_DOCTYPE, run_name, field) or 0
    set_fields(run_name, {field: current + amount})


def transition(run_name, from_status, to_status, extra_fields=None) -> None:
    if not can_transition(from_status, to_status):
        frappe.throw(f"Cannot transition TTS Run from {from_status} to {to_status}")
    fields = dict(extra_fields or {})
    fields["status"] = to_status
    set_fields(run_name, fields)


def get_for_update(run_name):
    rows = frappe.db.sql(
        f"select * from `tab{RUN_DOCTYPE}` where name = %(name)s for update",
        {"name": run_name},
        as_dict=True,
    )
    return frappe._dict(rows[0]) if rows else None


def conditional_write_url(feedback_request_name: str, url: str, content_hash: str, expected_modified) -> int:
    frappe.db.sql(
        """
        UPDATE `tabFeedback Request`
        SET audio_feedback_url = %(url)s,
            audio_content_hash = %(content_hash)s,
            audio_status = 'Completed',
            audio_completed_at = %(now)s
        WHERE name = %(name)s
          AND (audio_feedback_url IS NULL OR audio_feedback_url = '')
          AND audio_status = 'Processing'
          AND modified = %(expected_modified)s
        """,
        {
            "name": feedback_request_name, "url": url, "content_hash": content_hash,
            "expected_modified": expected_modified, "now": frappe.utils.now_datetime(),
        },
    )
    cursor = frappe.db._cursor
    return getattr(cursor, "rowcount", 0)


def mark_failed(feedback_request_name: str, reason: str) -> None:
    frappe.db.set_value(TARGET_DOCTYPE, feedback_request_name, {
        "audio_status": "Failed",
        "audio_error": (reason or "")[:140],
    }, update_modified=False)