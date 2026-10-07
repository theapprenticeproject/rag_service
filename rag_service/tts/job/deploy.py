import frappe

from ..constants import (
    ACTIVE_STATUSES, DECISION_DEPLOY, DECISION_SKIP_BLOCKED, DECISION_SKIP_BUDGET,
    DECISION_SKIP_DISABLED, DECISION_SKIP_NOTHING, DECISION_SKIP_BELOW_MINIMUM,
    RUN_DOCTYPE, STATUS_DEPLOYING, STATUS_DEPLOY_FAILED, STATUS_SKIPPED, STATUS_STARTING,
    TERMINATION_MANUAL_ACTION_NEEDED, TERMINATION_PENDING,
)
from ..lib import budget as budget_lib
from ..lib import runlog, runstate
from ..lib import settings as settings_lib
from ..lib import tokens
from ..lib.eligibility import find_eligible
from ..lib.runpod import RunPodClient, RunPodError, build_create_payload, is_capacity_error

LOCK_KEY = "tts:deploy:mutex"
LOCK_TTL_SECONDS = 300
WAITING_FOR_CAPACITY = "waiting_for_capacity"


def _acquire_mutex():
    return bool(frappe.cache().set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SECONDS))


def _release_mutex():
    frappe.cache().delete(LOCK_KEY)


def _blocker(settings):
    if not settings.kill_switch_on():
        return DECISION_SKIP_DISABLED, "TTS Settings.enabled is off"
    if not (settings.image_name or "").strip() or not (settings.model_revision or "").strip():
        return DECISION_SKIP_BLOCKED, "image_name or model_revision is empty"
    missing = [f for f in ("runpod_api_key", "tts_pod_shared_secret", "tts_gcs_service_account_json_b64")
               if not settings.get_secret(f)]
    if missing:
        return DECISION_SKIP_BLOCKED, "missing secrets: " + ",".join(missing)
    if frappe.db.exists(RUN_DOCTYPE, {"status": ["in", list(ACTIVE_STATUSES)]}):
        return DECISION_SKIP_BLOCKED, "another run is active"
    if frappe.db.exists(
        RUN_DOCTYPE,
        {"termination_status": ["in", [TERMINATION_PENDING, TERMINATION_MANUAL_ACTION_NEEDED]]},
    ):
        return DECISION_SKIP_BLOCKED, "a pod termination is unverified"
    return None


def _new_run(settings, trigger_type, triggered_by, status, extra):
    doc = frappe.get_doc({
        "doctype": RUN_DOCTYPE,
        "status": status,
        "trigger_type": trigger_type,
        "triggered_by": triggered_by or frappe.session.user,
        "created_at": frappe.utils.now_datetime(),
        "termination_status": "Not Needed",
        **extra,
    })
    doc.insert(ignore_permissions=True)
    return doc


def _env(settings, run_name, count):
    shared = settings.get_secret("tts_pod_shared_secret")
    ttl_minutes = int(settings.pod_limit_max_seconds / 60) + int(settings.token_buffer_minutes)
    return {
        "SERVER_URL": settings.public_base_url,
        "API_PATH_PREFIX": settings_lib.api_path_prefix(settings),
        "RUN_ID": run_name,
        "RUN_TOKEN": tokens.issue(run_name, shared, ttl_minutes),
        "GCS_BUCKET": settings.gcs_bucket,
        "GCS_PREFIX": settings.gcs_prefix,
        "CDN_BASE_URL": settings.cdn_base_url,
        "MODEL_REVISION": settings.model_revision,
        "GCS_SERVICE_ACCOUNT_JSON_B64": settings.get_secret("tts_gcs_service_account_json_b64"),
        "RUNPOD_API_KEY": settings.get_secret("runpod_api_key"),
        "POD_LIMIT_SECONDS": str(settings.pod_limit_max_seconds),
        "MAX_ITEMS": str(count),
        "MAX_TEXT_CHARS": str(settings.max_text_chars()),
    }


def _record_pod(run_name, pod):
    runstate.set_fields(run_name, {
        "pod_id": pod.get("id") or pod.get("podId"),
        "pod_name": pod.get("name"),
        "hourly_rate": pod.get("costPerHr") or 0,
        "pod_created_at": frappe.utils.now_datetime(),
        "next_capacity_retry_at": None,
        "status": STATUS_STARTING,
    })


def _mark_deploy_failed(run_name, reason):
    runstate.set_fields(run_name, {
        "status": STATUS_DEPLOY_FAILED,
        "status_reason": str(reason)[:140],
        "next_capacity_retry_at": None,
    })


def _schedule_capacity_retry(settings, run_name, detail):
    row = frappe.db.get_value(
        RUN_DOCTYPE, run_name, ["capacity_wait_started_at", "capacity_retry_count"], as_dict=True
    )
    now = frappe.utils.now_datetime()
    started = row.capacity_wait_started_at or now
    attempts = (row.capacity_retry_count or 0) + 1
    deadline = frappe.utils.add_to_date(started, minutes=60)
    next_at = frappe.utils.add_to_date(now, minutes=3)
    if next_at > deadline:
        runlog.append(run_name, f"no capacity after {attempts} attempts, giving up")
        _mark_deploy_failed(run_name, f"no_capacity_after_{attempts}_attempts")
        frappe.db.commit()
        return {"deployed": False, "reason": STATUS_DEPLOY_FAILED, "run": run_name}
    runstate.set_fields(run_name, {
        "capacity_wait_started_at": started,
        "capacity_retry_count": attempts,
        "next_capacity_retry_at": next_at,
        "status_reason": f"{WAITING_FOR_CAPACITY} attempt {attempts}",
    })
    runlog.append(run_name, f"no capacity (attempt {attempts}), retry at {next_at}: {detail}")
    frappe.db.commit()
    return {"deployed": False, "reason": WAITING_FOR_CAPACITY, "run": run_name, "retry_at": str(next_at)}


def _launch(settings, run_name, count):
    try:
        client = RunPodClient(settings.runpod_api_base, settings.get_secret("runpod_api_key"),
                               settings.http_timeout_seconds)
        payload = build_create_payload(settings, run_name, _env(settings, run_name, count))
        try:
            pod = client.create_pod(payload)
        except RunPodError as exc:
            existing = None
            try:
                existing = client.find_by_name(run_name)
            except RunPodError:
                pass
            if existing:
                _record_pod(run_name, existing)
                frappe.db.commit()
                return {"deployed": True, "adopted": True, "run": run_name}
            if is_capacity_error(exc):
                return _schedule_capacity_retry(settings, run_name, str(exc))
            _mark_deploy_failed(run_name, str(exc))
            frappe.db.commit()
            return {"deployed": False, "reason": STATUS_DEPLOY_FAILED, "run": run_name}
        _record_pod(run_name, pod)
        frappe.db.commit()
        return {"deployed": True, "run": run_name}
    except Exception as exc:
        frappe.log_error(title="tts_launch_failed")
        _mark_deploy_failed(run_name, f"{type(exc).__name__}: {exc}")
        frappe.db.commit()
        return {"deployed": False, "reason": STATUS_DEPLOY_FAILED, "run": run_name}


def retry_capacity(run_name):
    if not _acquire_mutex():
        return {"deployed": False, "reason": DECISION_SKIP_BLOCKED}
    try:
        settings = settings_lib.load_fresh()
        row = frappe.db.get_value(RUN_DOCTYPE, run_name, ["status", "pod_id", "manifest_count"], as_dict=True)
        if not row or row.status != STATUS_DEPLOYING or row.pod_id:
            return {"deployed": False, "reason": "not_waiting"}
        if not settings.kill_switch_on():
            _mark_deploy_failed(run_name, "disabled_while_waiting_for_capacity")
            frappe.db.commit()
            return {"deployed": False, "reason": DECISION_SKIP_DISABLED, "run": run_name}
        lease = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=3)
        runstate.set_fields(run_name, {"next_capacity_retry_at": lease})
        frappe.db.commit()
        return _launch(settings, run_name, row.manifest_count or 0)
    finally:
        _release_mutex()


def deploy_new(trigger_type="Manual", triggered_by=None, force=False) -> dict:
    if not _acquire_mutex():
        return {"deployed": False, "reason": DECISION_SKIP_BLOCKED, "detail": "another deploy is in progress"}
    try:
        settings = settings_lib.load_fresh()

        blocker = _blocker(settings)
        if blocker:
            decision, reason = blocker
            run = _new_run(settings, trigger_type, triggered_by, STATUS_SKIPPED,
                            {"decision": decision, "status_reason": reason})
            frappe.db.commit()
            return {"deployed": False, "reason": decision, "run": run.name}

        found = find_eligible(settings, max_scan=settings.max_clips_per_run * 3)
        if not found.items:
            run = _new_run(settings, trigger_type, triggered_by, STATUS_SKIPPED,
                            {"decision": DECISION_SKIP_NOTHING, "status_reason": "no Queued rows found",
                             "eligible_found": 0})
            frappe.db.commit()
            return {"deployed": False, "reason": DECISION_SKIP_NOTHING, "run": run.name}

        allow_bypass = force and settings.allow_force_start
        if len(found.items) < settings.min_items_to_deploy and not allow_bypass:
            run = _new_run(settings, trigger_type, triggered_by, STATUS_SKIPPED,
                            {"decision": DECISION_SKIP_BELOW_MINIMUM,
                             "status_reason": f"{len(found.items)} eligible, minimum is {settings.min_items_to_deploy}",
                             "eligible_found": len(found.items)})
            frappe.db.commit()
            return {"deployed": False, "reason": DECISION_SKIP_BELOW_MINIMUM, "run": run.name}

        check = budget_lib.fit_to_caps(settings, found.items)
        if not check.fits:
            run = _new_run(settings, trigger_type, triggered_by, STATUS_SKIPPED,
                            {"decision": DECISION_SKIP_BUDGET, "status_reason": check.reason,
                             "eligible_found": len(found.items), "estimated_cost_usd": check.estimate.cost_usd})
            frappe.db.commit()
            return {"deployed": False, "reason": DECISION_SKIP_BUDGET, "run": run.name}

        items = found.items[: check.trimmed_count]
        run = _new_run(settings, trigger_type, triggered_by, STATUS_DEPLOYING, {
            "decision": DECISION_DEPLOY,
            "eligible_found": len(found.items),
            "manifest_count": len(items),
            "estimated_cost_usd": check.estimate.cost_usd,
        })

        for item in items:
            frappe.db.set_value("Feedback Request", item.id, {
                "audio_status": "Processing", "audio_run": run.name,
            }, update_modified=False)
        frappe.db.commit()

        return _launch(settings, run.name, len(items))
    finally:
        _release_mutex()