import frappe

from ..constants import BAD_RUN_STATUSES, RUN_DOCTYPE, SETTINGS_DOCTYPE, STATUS_COMPLETED_WITH_FAILURES, \
    STATUS_DRAFT, STATUS_SKIPPED, STATUS_STOPPED


def _success_percent(run):
    total = (run.written or 0) + (run.pod_failed or 0) + (run.not_processed or 0)
    if not total:
        return 100
    return int(round(100.0 * (run.written or 0) / total))


def _is_bad(run, settings_doc):
    if run.status in BAD_RUN_STATUSES:
        return True
    if run.status == STATUS_COMPLETED_WITH_FAILURES:
        return _success_percent(run) < settings_doc.bad_run_success_floor_percent
    return False


def evaluate(latest_run_name):
    settings_doc = frappe.get_single(SETTINGS_DOCTYPE)
    threshold = int(settings_doc.max_consecutive_bad_runs or 2)
    recent = frappe.get_all(
        RUN_DOCTYPE,
        filters={"status": ["not in", [STATUS_DRAFT, STATUS_SKIPPED, STATUS_STOPPED]]},
        fields=["name", "status", "written", "pod_failed", "not_processed"],
        order_by="creation desc",
        limit_page_length=threshold,
    )
    if len(recent) < threshold:
        return False
    if all(_is_bad(run, settings_doc) for run in recent):
        _trip(settings_doc)
        return True
    return False


def _trip(settings_doc):
    if not settings_doc.enabled:
        return
    frappe.db.set_single_value(SETTINGS_DOCTYPE, "enabled", 0)
    frappe.db.commit()
    if settings_doc.alert_email:
        frappe.sendmail(
            recipients=[settings_doc.alert_email],
            subject="TTS circuit breaker tripped",
            message=(
                "TTS batching has been disabled automatically after consecutive bad runs. "
                "Check the TTS Run list and re-enable in TTS Settings once resolved."
            ),
        )
