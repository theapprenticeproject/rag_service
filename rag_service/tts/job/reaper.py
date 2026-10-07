import asyncio

import frappe

from ..constants import ACTIVE_STATUSES, RUN_DOCTYPE, STATUS_DEPLOYING, STATUS_DEPLOY_FAILED, \
    STATUS_LOST, STATUS_RUNNING, STATUS_STARTING, STATUS_TIMED_OUT
from ..lib import accounting, runlog, runstate
from ..lib import settings as settings_lib
from ..lib.runpod import RunPodClient


def _client(settings):
    return RunPodClient(settings.runpod_api_base, settings.get_secret("runpod_api_key"), settings.http_timeout_seconds)


def _release_stuck_rows(run_name, target_status):
    from rag_service.core.feedback_service import FeedbackService

    stuck = frappe.get_all(
        "Feedback Request",
        filters={"audio_run": run_name, "audio_status": "Processing"},
        pluck="name",
    )
    fs = FeedbackService()
    for fr_name in stuck:
        frappe.db.set_value("Feedback Request", fr_name, {
            "audio_status": "Failed",
            "audio_error": f"run_{target_status.lower().replace(' ', '_')}",
        }, update_modified=False)
        frappe.db.commit()
        asyncio.run(fs.release_to_lms_if_ready(fr_name))
    return len(stuck)


def _finalize(run, target_status, extra=None):
    fields = dict(extra or {})
    fields["terminated_at"] = frappe.utils.now_datetime()
    settings = settings_lib.load()

    # FIX: not_processed must be computed here, BEFORE the circuit breaker runs.
    # pod.py's complete() is the only other writer of not_processed, and a reaped
    # run (timeout/lost) never reaches complete(). Without this, breaker.evaluate()
    # divides written/(written+pod_failed) with not_processed implicitly 0, which
    # overstates success percent for exactly the runs most likely to be bad —
    # the ones that stalled with most items never attempted.
    manifest_count = run.manifest_count or 0
    accounted = (run.written or 0) + (run.pod_failed or 0)
    not_processed = max(0, manifest_count - accounted)
    fields["not_processed"] = not_processed

    merged = dict(run.as_dict())
    merged.update(fields)
    fields.update(accounting.summarize(merged, run.hourly_rate or settings.fallback_hourly_rate_usd))
    fields["termination_status"] = "Pending"
    runstate.transition(run.name, run.status, target_status, fields)
    frappe.db.commit()

    released = _release_stuck_rows(run.name, target_status)
    runlog.append(
        run.name,
        f"reaper finalize: status={target_status} manifest={manifest_count} "
        f"written={run.written or 0} pod_failed={run.pod_failed or 0} "
        f"not_processed={not_processed} released={released}",
    )

    try:
        client = _client(settings)
        if run.pod_id:
            client.delete_pod(run.pod_id)
    except Exception:
        frappe.log_error(title="tts_reaper_delete_failed")

    try:
        frappe.enqueue(
            "rag_service.tts.job.termination.verify_termination",
            queue="long", run_name=run.name, enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="tts_reaper_enqueue_termination_failed")

    try:
        from ..lib import breaker
        breaker.evaluate(run.name)
    except Exception:
        frappe.log_error(title="tts_reaper_breaker_failed")


def _handle_capacity_wait(run, run_name, now):
    retry_at = run.get("next_capacity_retry_at")
    if retry_at and now >= frappe.utils.get_datetime(retry_at):
        frappe.enqueue(
            "rag_service.tts.job.deploy.retry_capacity",
            queue="long", job_id=f"tts-capacity-retry-{run_name}", deduplicate=True,
            run_name=run_name, enqueue_after_commit=True,
        )
        runlog.append(run_name, "reaper: capacity retry enqueued")
        return True
    return False


def reap_one(run_name) -> bool:
    settings = settings_lib.load()
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if run.status not in ACTIVE_STATUSES:
        return False

    now = frappe.utils.now_datetime()

    if run.status == STATUS_STARTING and run.pod_created_at:
        elapsed_minutes = frappe.utils.time_diff_in_hours(now, run.pod_created_at) * 60
        if elapsed_minutes > settings.startup_deadline_minutes:
            _finalize(run, STATUS_TIMED_OUT, {"status_reason": "startup_deadline_exceeded"})
            runlog.append(run_name, "reaper: startup deadline exceeded")
            return True

    if run.status == STATUS_RUNNING and run.last_beat_at:
        idle_minutes = frappe.utils.time_diff_in_hours(now, run.last_beat_at) * 60
        if idle_minutes > settings.heartbeat_timeout_minutes:
            _finalize(run, STATUS_LOST, {"status_reason": "heartbeat_timeout"})
            runlog.append(run_name, "reaper: heartbeat timeout")
            return True

    if run.status in ACTIVE_STATUSES and run.pod_created_at:
        max_minutes = (settings.pod_limit_max_seconds / 60.0) + settings.token_buffer_minutes
        elapsed_minutes = frappe.utils.time_diff_in_hours(now, run.pod_created_at) * 60
        if elapsed_minutes > max_minutes:
            _finalize(run, STATUS_TIMED_OUT, {"status_reason": "pod_limit_exceeded"})
            runlog.append(run_name, "reaper: pod limit exceeded")
            return True

    if run.status == STATUS_DEPLOYING and not run.pod_id:
        if run.get("capacity_wait_started_at"):
            return _handle_capacity_wait(run, run_name, now)
        age_minutes = frappe.utils.time_diff_in_hours(now, run.creation) * 60
        if age_minutes > 5:
            client = _client(settings)
            adopted = client.find_by_name(run_name)
            if adopted:
                pod_id = adopted.get("id") or adopted.get("podId")
                runstate.set_fields(run_name, {
                    "pod_id": pod_id, "status": STATUS_STARTING, "pod_created_at": frappe.utils.now_datetime(),
                })
                frappe.db.commit()
                runlog.append(run_name, f"reaper: adopted orphaned pod {pod_id}")
            else:
                runstate.set_fields(run_name, {"status": STATUS_DEPLOY_FAILED, "status_reason": "no_pod_found_by_reaper"})
                for item_name in frappe.get_all(
                    "Feedback Request", filters={"audio_run": run_name, "audio_status": "Processing"}, pluck="name",
                ):
                    frappe.db.set_value("Feedback Request", item_name, {
                        "audio_status": "Queued", "audio_run": None,
                    }, update_modified=False)
                frappe.db.commit()
                runlog.append(run_name, "reaper: deploying with no pod, none found")
            return True

    return False


def _reap_stale_drafts():
    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-24)
    rows = frappe.get_all(RUN_DOCTYPE, filters={"status": "Draft", "creation": ["<", cutoff]}, fields=["name"])
    for row in rows:
        runstate.set_fields(row.name, {"status": "Skipped", "status_reason": "draft_expired"})
    if rows:
        frappe.db.commit()


def _reverify_termination():
    from .termination import verify_termination
    rows = frappe.get_all(
        RUN_DOCTYPE, filters={"termination_status": ["in", ["Pending", "Manual Action Needed"]]}, fields=["name"],
    )
    for row in rows:
        try:
            verify_termination(row.name)
        except Exception:
            frappe.log_error(title="tts_reaper_reverify_termination_failed")


def run_reaper_now() -> dict:
    acted = 0
    for row in frappe.get_all(RUN_DOCTYPE, filters={"status": ["in", list(ACTIVE_STATUSES)]}, fields=["name"]):
        try:
            if reap_one(row.name):
                acted += 1
        except Exception:
            frappe.log_error(title="tts_reaper_failed")
    _reverify_termination()
    _reap_stale_drafts()
    return {"acted": acted}