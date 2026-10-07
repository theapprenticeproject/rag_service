import frappe

from ..constants import ACTIVE_STATUSES
from ..lib import budget as budget_lib
from ..lib import runstate
from ..lib import settings as settings_lib
from ..lib.eligibility import find_eligible
from ..lib.runpod import RunPodClient


def _require_system_manager():
    if "System Manager" not in frappe.get_roles():
        frappe.throw("Not permitted", frappe.PermissionError)


def _image_is_immutable(image_name):
    name = (image_name or "").strip().lower()
    if not name:
        return False
    if "@sha256:" in name:
        return True
    last = name.rsplit("/", 1)[-1]
    tag = last.split(":", 1)[1] if ":" in last else ""
    return tag not in ("", "stable", "latest")


@frappe.whitelist()
def preview_eligible():
    _require_system_manager()
    settings = settings_lib.load()
    found = find_eligible(settings, max_scan=settings.max_clips_per_run * 3)
    check = budget_lib.fit_to_caps(settings, found.items) if found.items else None
    return {
        "eligible_found": len(found.items),
        "skipped_unsupported_language": found.skipped_unsupported_language,
        "skipped_empty_text": found.skipped_empty_text,
        "skipped_stale_fingerprint": found.skipped_stale_fingerprint,
        "would_deploy_count": check.trimmed_count if check else 0,
        "estimated_cost_usd": check.estimate.cost_usd if check else 0,
        "today_spend_usd": check.today_spend_usd if check else 0,
        "month_spend_usd": check.month_spend_usd if check else 0,
    }


@frappe.whitelist()
def start_batch_manual(force=False):
    _require_system_manager()
    from ..job.deploy import deploy_new
    return deploy_new(trigger_type="Manual", triggered_by=frappe.session.user, force=bool(force))


@frappe.whitelist()
def cancel_run(run_name):
    _require_system_manager()
    run = frappe.get_doc("TTS Run", run_name)
    if run.status not in ACTIVE_STATUSES:
        frappe.throw("Run is not active")
    runstate.transition(run_name, run.status, "Stopping")
    return {"status": "Stopping"}


@frappe.whitelist()
def force_terminate(run_name):
    _require_system_manager()
    from ..job.termination import force_delete_now
    return force_delete_now(run_name)


@frappe.whitelist()
def verify_termination(run_name):
    _require_system_manager()
    from ..job.termination import verify_termination as verify
    return verify(run_name)


@frappe.whitelist()
def run_reaper_now():
    _require_system_manager()
    from ..job.reaper import run_reaper_now as _run
    return _run()


@frappe.whitelist()
def check_setup():
    _require_system_manager()
    settings = settings_lib.load()
    results = {}

    results["runpod_api_key"] = {"ok": bool(settings.get_secret("runpod_api_key"))}
    results["tts_pod_shared_secret"] = {"ok": bool(settings.get_secret("tts_pod_shared_secret"))}
    results["tts_gcs_service_account_json_b64"] = {"ok": bool(settings.get_secret("tts_gcs_service_account_json_b64"))}
    results["https_base_url"] = {"ok": settings.public_base_url.startswith("https://"), "value": settings.public_base_url}
    results["gpu_type_ids"] = {"ok": bool(settings.gpu_type_ids)}
    results["image_name"] = {"ok": _image_is_immutable(settings.image_name), "value": settings.image_name or "empty"}
    results["model_revision"] = {"ok": bool((settings.model_revision or "").strip()), "value": settings.model_revision or "empty"}
    results["alert_email"] = {"ok": bool((settings.alert_email or "").strip())}

    try:
        client = RunPodClient(settings.runpod_api_base, settings.get_secret("runpod_api_key"), settings.http_timeout_seconds)
        client.ping()
        results["runpod_key"] = {"ok": True}
    except Exception as exc:
        results["runpod_key"] = {"ok": False, "error": str(exc)}

    results["all_ok"] = all(v.get("ok") for v in results.values() if isinstance(v, dict))
    return results