import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

import frappe
import requests

from ..constants import (
    FORMAT_OGG, HEAD_CHECK_BATCH_BUDGET_SECONDS, HEAD_CHECK_MAX_WORKERS,
    HEAD_CHECK_TIMEOUT_SECONDS, PROGRESS_MAX_RECORDS, STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_FAILURES, STATUS_FAILED, STATUS_RUNNING, STATUS_STARTING,
    STATUS_STOPPING, STATUS_TIMED_OUT,
)
from ..lib import contract, runlog, runstate
from ..lib.guard import GuardStop, enter
from ..lib.release import release_stuck_rows
from ..lib.settings import load as load_settings
from ..lib.text import prepare_item

KNOWN_COMPLETE_REASONS = frozenset({
    "completed", "empty", "completed_with_failures", "stopped", "timeout", "startup_timeout",
})


def _respond_stop(exc):
    frappe.local.response.http_status_code = exc.http_code
    return {"error": exc.message}


def _no_store():
    frappe.local.response["Cache-Control"] = "no-store"


def _num(value, default=0):
    return value if value is not None else default


def _feedback_language_and_source(fr_name):
    row = frappe.db.get_value(
        "Feedback Request", fr_name,
        ["generated_feedback", "audio_source_text", "modified"], as_dict=True,
    )
    if not row:
        return None
    try:
        feedback = json.loads(row.generated_feedback or "{}")
    except Exception:
        feedback = {}
    language = feedback.get("translation_language") or "English"
    return frappe._dict(
        language=language, audio_source_text=row.audio_source_text, modified=row.modified
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def manifest():
    _no_store()
    try:
        run = enter("manifest")
    except GuardStop as exc:
        return _respond_stop(exc)

    settings = load_settings()

    if run.status not in (STATUS_STARTING, STATUS_RUNNING):
        frappe.local.response.http_status_code = 409
        return {"error": "wrong_phase"}

    if run.manifest_served:
        rows = frappe.get_all(
            "Feedback Request",
            filters={"audio_run": run.name, "audio_status": "Processing"},
            fields=["name", "audio_source_text", "audio_text_fingerprint"],
        )
        items = []
        for row in rows:
            info = _feedback_language_and_source(row.name)
            if info is None:
                continue
            prepared = prepare_item(
                info.audio_source_text, info.language, settings.max_text_chars(), settings.over_length_policy
            )
            if prepared is None or prepared.fingerprint != row.audio_text_fingerprint:
                continue
            items.append({"id": row.name, "text": prepared.text, "language": prepared.language, "format": FORMAT_OGG})
        runlog.append(run.name, f"manifest re-served {len(items)} items (cached)")
        return {"items": items}

    fields = {"manifest_served": 1}
    if run.status == STATUS_STARTING:
        fields["status"] = STATUS_RUNNING
        fields["last_beat_at"] = frappe.utils.now_datetime()
    runstate.set_fields(run.name, fields)

    rows = frappe.get_all(
        "Feedback Request",
        filters={"audio_run": run.name, "audio_status": "Processing"},
        fields=["name", "audio_source_text", "audio_text_fingerprint"],
    )

    items = []
    stale = 0
    for row in rows:
        info = _feedback_language_and_source(row.name)
        if info is None:
            stale += 1
            continue
        prepared = prepare_item(
            info.audio_source_text, info.language, settings.max_text_chars(), settings.over_length_policy
        )
        if prepared is None or prepared.fingerprint != row.audio_text_fingerprint:
            stale += 1
            continue
        items.append({"id": row.name, "text": prepared.text, "language": prepared.language, "format": FORMAT_OGG})

    runstate.set_fields(run.name, {"manifest_count": max(run.manifest_count or 0, len(items))})
    runlog.append(run.name, f"manifest served {len(items)} items, {stale} stale")
    return {"items": items}


def _expected_for(item_id, settings):
    info = _feedback_language_and_source(item_id)
    if info is None:
        return None, None, None
    prepared = prepare_item(
        info.audio_source_text, info.language, settings.max_text_chars(), settings.over_length_policy
    )
    if prepared is None:
        return None, None, None
    expected_hash = contract.expected_hash(prepared.text, prepared.language, settings.model_revision, FORMAT_OGG)
    expected_url = contract.expected_url(
        prepared.text, prepared.language, settings.model_revision, settings.gcs_prefix, settings.cdn_base_url, FORMAT_OGG
    )
    return expected_hash, expected_url, info.modified


def _head_check(url):
    try:
        response = requests.head(url, timeout=HEAD_CHECK_TIMEOUT_SECONDS, allow_redirects=True)
        return response.status_code == 200
    except Exception:
        return False


def _verify_urls(urls):
    if not urls:
        return {}
    verified = {}
    deadline = time.monotonic() + HEAD_CHECK_BATCH_BUDGET_SECONDS
    workers = min(HEAD_CHECK_MAX_WORKERS, max(1, len(urls)))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(_head_check, url): url for url in urls}
    try:
        remaining = max(0.1, deadline - time.monotonic())
        for future in as_completed(futures, timeout=remaining):
            url = futures[future]
            try:
                verified[url] = bool(future.result())
            except Exception:
                verified[url] = False
    except FuturesTimeoutError:
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    for url in urls:
        verified.setdefault(url, False)
    return verified


@frappe.whitelist(allow_guest=True, methods=["POST"])
def progress():
    _no_store()
    try:
        run = enter("progress")
    except GuardStop as exc:
        return _respond_stop(exc)

    settings = load_settings()

    from rag_service.core.feedback_service import FeedbackService
    fs = FeedbackService()

    records = frappe.local.form_dict.get("records") or []
    if not isinstance(records, list):
        records = []
    records = [r for r in records if isinstance(r, dict)][:PROGRESS_MAX_RECORDS]

    written = already_set = stale = hash_mismatch = bad_url = pod_failed = 0
    urls_to_verify = set()
    pending = []

    for record in records:
        item_id = record.get("id")
        if not item_id:
            continue

        if record.get("status") == "failed":
            runstate.mark_failed(item_id, record.get("error") or "pod_reported_failure")
            asyncio.run(fs.release_to_lms_if_ready(item_id))
            pod_failed += 1
            continue

        current_status = frappe.db.get_value("Feedback Request", item_id, "audio_status")
        if current_status != "Processing":
            already_set += 1
            continue

        expected_hash, expected_url, expected_modified = _expected_for(item_id, settings)
        if expected_hash is None:
            stale += 1
            continue

        reported_hash = record.get("content_hash")
        reported_url = record.get("url")

        if not contract.hash_matches(reported_hash, expected_hash):
            hash_mismatch += 1
            continue
        if not contract.url_matches(reported_url, expected_url):
            bad_url += 1
            continue

        needs_verify = bool(settings.verify_urls_on_write)
        if needs_verify:
            urls_to_verify.add(reported_url)
        pending.append((item_id, reported_url, reported_hash, expected_modified, needs_verify))

    verified = _verify_urls(urls_to_verify)

    for item_id, reported_url, reported_hash, expected_modified, needs_verify in pending:
        if needs_verify and not verified.get(reported_url):
            bad_url += 1
            continue
        affected = runstate.conditional_write_url(item_id, reported_url, reported_hash, expected_modified)
        if affected and affected > 0:
            written += 1
            asyncio.run(fs.release_to_lms_if_ready(item_id))
        else:
            already_set += 1

    for field, value in (
        ("written", written), ("already_set", already_set), ("stale", stale),
        ("hash_mismatch", hash_mismatch), ("bad_url", bad_url), ("pod_failed", pod_failed),
    ):
        if value:
            runstate.increment(run.name, field, value)

    runstate.set_fields(run.name, {"last_progress_at": frappe.utils.now_datetime(), "last_beat_at": frappe.utils.now_datetime()})
    runlog.append(
        run.name,
        f"progress batch of {len(records)}: written={written} already_set={already_set} "
        f"stale={stale} hash_mismatch={hash_mismatch} bad_url={bad_url} pod_failed={pod_failed}",
    )

    return {"accepted": True, "written": written, "pod_failed": pod_failed}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def beat():
    _no_store()
    try:
        run = enter("beat")
    except GuardStop as exc:
        return _respond_stop(exc)

    settings = load_settings()
    phase = frappe.local.form_dict.get("phase")
    remaining = frappe.local.form_dict.get("remaining")
    try:
        remaining = int(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining = None

    fields = {"last_beat_at": frappe.utils.now_datetime(), "last_phase": phase, "remaining_reported": remaining}
    if run.status == STATUS_STARTING:
        fields["status"] = STATUS_RUNNING
    runstate.set_fields(run.name, fields)

    should_stop = run.status == STATUS_STOPPING or not settings.kill_switch_on()
    return {"stop": bool(should_stop)}


def _final_status(pod_reason, run):
    pod_reason = str(pod_reason or "")
    manifest_count = run.manifest_count or 0
    accounted = (run.written or 0) + (run.pod_failed or 0)
    if pod_reason not in KNOWN_COMPLETE_REASONS:
        runlog.append(run.name, f"complete: unrecognized reason '{pod_reason}', treating as failure")
        return STATUS_FAILED
    if pod_reason in ("completed", "empty"):
        if manifest_count == 0 or accounted >= manifest_count:
            return STATUS_COMPLETED
        return STATUS_COMPLETED_WITH_FAILURES
    if pod_reason == "completed_with_failures":
        return STATUS_COMPLETED_WITH_FAILURES
    if pod_reason == "stopped":
        return "Stopped"
    if pod_reason in ("timeout", "startup_timeout"):
        return STATUS_TIMED_OUT
    return STATUS_FAILED


@frappe.whitelist(allow_guest=True, methods=["POST"])
def complete():
    _no_store()
    try:
        run = enter("complete")
    except GuardStop as exc:
        return _respond_stop(exc)

    reason = str(frappe.local.form_dict.get("reason") or "exception")
    stats = frappe.local.form_dict.get("stats")
    if not isinstance(stats, dict):
        stats = {}

    manifest_count = run.manifest_count or 0
    accounted = (run.written or 0) + (run.pod_failed or 0)
    not_processed = max(0, manifest_count - accounted)

    final_status = _final_status(reason, run)

    fields = {
        "completion_reason": reason[:140],
        "not_processed": not_processed,
        "pod_uploaded": _num(stats.get("uploaded"), 0),
        "pod_generated": _num(stats.get("generated"), 0),
        "pod_decoded": _num(stats.get("decoded"), 0),
        "pod_truncated": _num(stats.get("truncated"), 0),
        "pod_retried": _num(stats.get("retried"), 0),
        "pod_tokens": _num(stats.get("tokens"), 0),
        "pod_audio_seconds": _num(stats.get("audio_seconds"), 0.0),
        "pod_startup_seconds": _num(stats.get("startup_seconds"), 0.0),
        "pod_run_seconds": _num(stats.get("run_seconds"), 0.0),
        "termination_status": "Pending",
    }

    if runstate.can_transition(run.status, final_status):
        fields["status"] = final_status
    elif run.status == final_status:
        pass
    else:
        fields["status"] = STATUS_FAILED
        fields["status_reason"] = f"invalid_transition_{run.status}_to_{final_status}"

    runstate.set_fields(run.name, fields)
    runlog.append(run.name, f"complete reason={reason} final_status={fields.get('status', run.status)}")

    released = release_stuck_rows(run.name, fields.get("status", run.status))
    if released:
        runlog.append(run.name, f"complete released {released} stuck row(s) still Processing")

    try:
        from ..lib import breaker
        breaker.evaluate(run.name)
    except Exception:
        frappe.log_error(title="tts_breaker_evaluate_failed")

    try:
        frappe.enqueue(
            "rag_service.tts.job.termination.verify_termination",
            queue="long", run_name=run.name, enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="tts_enqueue_termination_failed")

    return {"accepted": True}