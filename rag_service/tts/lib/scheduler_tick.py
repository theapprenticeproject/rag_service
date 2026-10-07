import frappe

from ..constants import ACTIVE_STATUSES, RUN_DOCTYPE
from ..lib import settings as settings_lib
from ...rag_service.doctype.tts_settings.tts_settings import cron_matches_now

TICK_LOCK_KEY = "tts:scheduler_tick:mutex"
TICK_LOCK_TTL_SECONDS = 120
CAPACITY_RETRY_LOOKBACK_MINUTES = 90
CRON_LOOKBACK_MINUTES = 10
CRON_FORWARD_SCAN_MINUTES = 48 * 60


def _acquire_tick_lock():
    return bool(frappe.cache().set(TICK_LOCK_KEY, "1", nx=True, ex=TICK_LOCK_TTL_SECONDS))


def _release_tick_lock():
    frappe.cache().delete(TICK_LOCK_KEY)


def _has_active_run():
    return bool(frappe.db.exists(RUN_DOCTYPE, {"status": ["in", list(ACTIVE_STATUSES)]}))


def _retry_waiting_capacity_runs():
    from .deploy import retry_capacity

    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-CAPACITY_RETRY_LOOKBACK_MINUTES)
    rows = frappe.get_all(
        RUN_DOCTYPE,
        filters={
            "status": "Deploying",
            "pod_id": ["in", ["", None]],
            "next_capacity_retry_at": ["<=", frappe.utils.now_datetime()],
            "creation": [">=", cutoff],
        },
        fields=["name"],
    )
    retried = 0
    for row in rows:
        try:
            frappe.enqueue(
                "rag_service.tts.job.deploy.retry_capacity",
                queue="long",
                job_id=f"tts-capacity-retry-{row.name}",
                deduplicate=True,
                run_name=row.name,
                enqueue_after_commit=True,
            )
            retried += 1
        except Exception:
            frappe.log_error(title="tts_scheduler_capacity_retry_enqueue_failed")
    return retried


def _cron_lines(settings_doc):
    return [
        line.strip()
        for line in (settings_doc.schedule_cron_expressions or "").splitlines()
        if line.strip()
    ]


def _interval_due(settings_doc, now):
    last_run_at = settings_doc.last_scheduled_run_at
    if not last_run_at:
        return True
    minutes_since = frappe.utils.time_diff_in_hours(now, frappe.utils.get_datetime(last_run_at)) * 60
    return minutes_since >= (settings_doc.schedule_interval_minutes or 0)


def _cron_due(settings_doc, now):
    lines = _cron_lines(settings_doc)
    if not lines:
        return False
    last_run_at = settings_doc.last_scheduled_run_at
    start = frappe.utils.get_datetime(last_run_at) if last_run_at else frappe.utils.add_to_date(now, minutes=-1)
    minutes_back = min(CRON_LOOKBACK_MINUTES, max(0, int(frappe.utils.time_diff_in_hours(now, start) * 60)))
    for offset in range(minutes_back, -1, -1):
        candidate = frappe.utils.add_to_date(now, minutes=-offset)
        if candidate <= start:
            continue
        for line in lines:
            if cron_matches_now(line, candidate):
                return True
    return False


def _next_interval_run_at(settings_doc, now):
    last_run_at = settings_doc.last_scheduled_run_at
    base = frappe.utils.get_datetime(last_run_at) if last_run_at else now
    return frappe.utils.add_to_date(base, minutes=settings_doc.schedule_interval_minutes or 0)


def _next_cron_run_at(settings_doc, now):
    lines = _cron_lines(settings_doc)
    if not lines:
        return None, "no_future_match_found"
    for offset in range(1, CRON_FORWARD_SCAN_MINUTES + 1):
        candidate = frappe.utils.add_to_date(now, minutes=offset)
        for line in lines:
            if cron_matches_now(line, candidate):
                return candidate, ""
    return None, "no_future_match_found"


def _trigger_scheduled_deploy():
    from .deploy import deploy_new

    result = deploy_new(trigger_type="Cron", triggered_by="Scheduler")
    frappe.logger().info(f"tts scheduler tick fired: {result}")
    return result


def run_if_due():
    if not _acquire_tick_lock():
        return {"ran": False, "reason": "tick_locked"}
    try:
        settings_doc = frappe.get_single("TTS Settings")
        retried = _retry_waiting_capacity_runs()

        if not getattr(settings_doc, "scheduling_enabled", 0):
            frappe.db.set_single_value("TTS Settings", "last_scheduler_tick_at", frappe.utils.now_datetime())
            frappe.db.set_single_value("TTS Settings", "last_scheduler_skip_reason", "scheduling_disabled")
            frappe.db.commit()
            return {"ran": False, "reason": "scheduling_disabled", "capacity_retries": retried}

        now = frappe.utils.now_datetime()
        mode = settings_doc.schedule_mode or "Interval"

        due = _interval_due(settings_doc, now) if mode == "Interval" else _cron_due(settings_doc, now)

        if mode == "Interval":
            next_run_at = _next_interval_run_at(settings_doc, now)
            skip_reason = ""
        else:
            next_run_at, skip_reason = _next_cron_run_at(settings_doc, now)

        ran = False
        reason = "not_due"
        result = None

        if due:
            if not settings_lib.load_fresh().kill_switch_on():
                reason = "tts_disabled"
                frappe.db.set_single_value("TTS Settings", "last_scheduled_run_at", now)
            elif _has_active_run():
                reason = "run_already_active"
                frappe.db.set_single_value("TTS Settings", "last_scheduled_run_at", now)
            else:
                result = _trigger_scheduled_deploy()
                frappe.db.set_single_value("TTS Settings", "last_scheduled_run_at", now)
                ran = True
                reason = ""

        frappe.db.set_single_value("TTS Settings", "last_scheduler_tick_at", now)
        frappe.db.set_single_value("TTS Settings", "last_scheduler_skip_reason", reason or skip_reason)
        if next_run_at is not None:
            frappe.db.set_single_value("TTS Settings", "next_scheduled_run_at", next_run_at)
        frappe.db.commit()

        return {
            "ran": ran,
            "reason": reason or None,
            "result": result,
            "capacity_retries": retried,
        }
    except Exception:
        frappe.log_error(title="tts_scheduler_tick_failed")
        return {"ran": False, "reason": "exception"}
    finally:
        _release_tick_lock()