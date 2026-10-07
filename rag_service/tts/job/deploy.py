import frappe

from ..constants import (
    DECISION_DEPLOY, DECISION_SKIP_BELOW_MINIMUM, DECISION_SKIP_BLOCKED, DECISION_SKIP_BUDGET,
    DECISION_SKIP_DISABLED, DECISION_SKIP_NOTHING, RUN_DOCTYPE, STATUS_DEPLOYING, STATUS_DEPLOY_FAILED,
    STATUS_STARTING,
)
from ..lib import budget as budget_lib
from ..lib import runlog, runstate
from ..lib import settings as settings_lib
from ..lib import tokens
from ..lib.eligibility import find_eligible
from ..lib.runpod import RunPodClient, RunPodError, build_create_payload, is_capacity_error

can_transition = runstate.can_transition
is_terminal = runstate.is_terminal
set_fields = runstate.set_fields
increment = runstate.increment
transition = runstate.transition
get_for_update = runstate.get_for_update
conditional_write_url = runstate.conditional_write_url
mark_failed = runstate.mark_failed


def _client(settings):
    return RunPodClient(settings.runpod_api_base, settings.get_secret("runpod_api_key"), settings.http_timeout_seconds)


def _has_active_run():
    from ..constants import ACTIVE_STATUSES
    return bool(frappe.db.exists(RUN_DOCTYPE, {"status": ["in", list(ACTIVE_STATUSES)]}))


def _create_run_row(trigger_type, triggered_by, decision, eligible_found):
    doc = frappe.new_doc(RUN_DOCTYPE)
    doc.flags["_tts_internal_write"] = True
    doc.status = "Draft"
    doc.status_reason = ""
    doc.trigger_type = trigger_type
    doc.triggered_by = triggered_by
    doc.created_at = frappe.utils.now_datetime()
    doc.decision = decision
    doc.eligible_found = eligible_found
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _reserve_items(run_name, names):
    if not names:
        return 0
    placeholders = ", ".join(["%s"] * len(names))
    frappe.db.sql(
        f"""
        UPDATE `tabFeedback Request`
        SET audio_status = 'Processing', audio_run = %s
        WHERE name IN ({placeholders}) AND audio_status = 'Queued'
        """,
        [run_name] + list(names),
    )
    cursor = frappe.db._cursor
    return getattr(cursor, "rowcount", 0)


def _release_items(names):
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    frappe.db.sql(
        f"""
        UPDATE `tabFeedback Request`
        SET audio_status = 'Queued', audio_run = NULL
        WHERE name IN ({placeholders}) AND audio_status = 'Processing'
        """,
        list(names),
    )


def deploy_new(trigger_type="Manual", triggered_by="System", force=False):
    settings = settings_lib.load_fresh()

    if not settings.kill_switch_on():
        return {"deployed": False, "reason": DECISION_SKIP_DISABLED}

    if _has_active_run():
        return {"deployed": False, "reason": DECISION_SKIP_BLOCKED}

    found = find_eligible(settings, max_scan=settings.max_clips_per_run * 3)
    if not found.items:
        _create_run_row(trigger_type, triggered_by, DECISION_SKIP_NOTHING, 0)
        return {"deployed": False, "reason": DECISION_SKIP_NOTHING}

    allow_small = force and bool(settings.allow_force_start)
    if not allow_small and len(found.items) < settings.min_items_to_deploy:
        _create_run_row(trigger_type, triggered_by, DECISION_SKIP_BELOW_MINIMUM, len(found.items))
        return {"deployed": False, "reason": DECISION_SKIP_BELOW_MINIMUM, "eligible_found": len(found.items)}

    check = budget_lib.fit_to_caps(settings, found.items)
    if not check.fits or check.trimmed_count == 0:
        _create_run_row(trigger_type, triggered_by, DECISION_SKIP_BUDGET, len(found.items))
        return {"deployed": False, "reason": DECISION_SKIP_BUDGET, "eligible_found": len(found.items)}

    selected = found.items[: check.trimmed_count]
    run_name = _create_run_row(trigger_type, triggered_by, DECISION_DEPLOY, len(found.items))

    reserved_names = [item.id for item in selected]
    reserved_count = _reserve_items(run_name, reserved_names)
    frappe.db.commit()

    if reserved_count == 0:
        runstate.set_fields(run_name, {"status": STATUS_DEPLOY_FAILED, "status_reason": "no_items_reserved"})
        frappe.db.commit()
        return {"deployed": False, "reason": "no_items_reserved", "run": run_name}

    runstate.transition(run_name, "Draft", STATUS_DEPLOYING, {
        "manifest_count": reserved_count,
        "estimated_cost_usd": check.estimate.cost_usd,
        "hourly_rate": settings.fallback_hourly_rate_usd,
    })
    frappe.db.commit()

    ttl_minutes = int(settings.pod_limit_max_seconds / 60) + int(settings.token_buffer_minutes)
    token = tokens.issue(run_name, settings.get_secret("tts_pod_shared_secret"), ttl_minutes)
    prefix = settings_lib.api_path_prefix(settings)

    env = {
        "RUN_ID": run_name,
        "RUN_TOKEN": token,
        "MANIFEST_URL": f"{settings.public_base_url}{prefix}manifest",
        "PROGRESS_URL": f"{settings.public_base_url}{prefix}progress",
        "BEAT_URL": f"{settings.public_base_url}{prefix}beat",
        "COMPLETE_URL": f"{settings.public_base_url}{prefix}complete",
        "GCS_BUCKET": settings.gcs_bucket,
        "GCS_PREFIX": settings.gcs_prefix,
        "MODEL_REVISION": settings.model_revision,
        "GCS_SERVICE_ACCOUNT_JSON_B64": settings.get_secret("tts_gcs_service_account_json_b64"),
        "POD_LIMIT_MAX_SECONDS": str(settings.pod_limit_max_seconds),
        "HEARTBEAT_INTERVAL_SECONDS": "30",
    }
    payload = build_create_payload(settings, run_name, env)

    try:
        client = _client(settings)
        pod = client.create_pod(payload)
    except RunPodError as exc:
        _release_items(reserved_names)
        if is_capacity_error(exc) and settings.capacity_retry_enabled:
            retry_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=settings.capacity_retry_minutes)
            runstate.set_fields(run_name, {
                "status": STATUS_DEPLOYING,
                "status_reason": "waiting_for_capacity",
                "capacity_wait_started_at": frappe.utils.now_datetime(),
                "next_capacity_retry_at": retry_at,
            })
            frappe.db.commit()
            runlog.append(run_name, f"deploy: capacity unavailable, retry at {retry_at}")
            return {"deployed": False, "reason": "waiting_for_capacity", "run": run_name}
        runstate.transition(run_name, STATUS_DEPLOYING, STATUS_DEPLOY_FAILED, {"status_reason": str(exc)[:140]})
        frappe.db.commit()
        runlog.append(run_name, f"deploy: create_pod failed: {exc}")
        return {"deployed": False, "reason": "deploy_failed", "run": run_name}
    except Exception as exc:
        _release_items(reserved_names)
        runstate.transition(run_name, STATUS_DEPLOYING, STATUS_DEPLOY_FAILED, {"status_reason": str(exc)[:140]})
        frappe.db.commit()
        runlog.append(run_name, f"deploy: unexpected error: {exc}")
        return {"deployed": False, "reason": "deploy_failed", "run": run_name}

    pod_id = pod.get("id") or pod.get("podId")
    hourly_rate = pod.get("costPerHr") or settings.fallback_hourly_rate_usd

    runstate.transition(run_name, STATUS_DEPLOYING, STATUS_STARTING, {
        "pod_id": pod_id,
        "pod_name": run_name,
        "hourly_rate": hourly_rate,
        "pod_created_at": frappe.utils.now_datetime(),
    })
    frappe.db.commit()
    runlog.append(run_name, f"deploy: pod {pod_id} created, {reserved_count} items reserved")

    return {"deployed": True, "run": run_name, "pod_id": pod_id, "reserved_count": reserved_count}


def retry_capacity(run_name):
    settings = settings_lib.load_fresh()
    run = runstate.get_for_update(run_name)
    if run is None or run.status != STATUS_DEPLOYING or run.pod_id:
        return {"retried": False}

    env_prefix = settings_lib.api_path_prefix(settings)
    reserved_names = frappe.get_all(
        "Feedback Request", filters={"audio_run": run_name, "audio_status": "Processing"}, pluck="name",
    )
    ttl_minutes = int(settings.pod_limit_max_seconds / 60) + int(settings.token_buffer_minutes)
    token = tokens.issue(run_name, settings.get_secret("tts_pod_shared_secret"), ttl_minutes)
    env = {
        "RUN_ID": run_name,
        "RUN_TOKEN": token,
        "MANIFEST_URL": f"{settings.public_base_url}{env_prefix}manifest",
        "PROGRESS_URL": f"{settings.public_base_url}{env_prefix}progress",
        "BEAT_URL": f"{settings.public_base_url}{env_prefix}beat",
        "COMPLETE_URL": f"{settings.public_base_url}{env_prefix}complete",
        "GCS_BUCKET": settings.gcs_bucket,
        "GCS_PREFIX": settings.gcs_prefix,
        "MODEL_REVISION": settings.model_revision,
        "GCS_SERVICE_ACCOUNT_JSON_B64": settings.get_secret("tts_gcs_service_account_json_b64"),
        "POD_LIMIT_MAX_SECONDS": str(settings.pod_limit_max_seconds),
        "HEARTBEAT_INTERVAL_SECONDS": "30",
    }
    payload = build_create_payload(settings, run_name, env)

    try:
        client = _client(settings)
        pod = client.create_pod(payload)
    except RunPodError as exc:
        if is_capacity_error(exc) and settings.capacity_retry_enabled:
            retry_count = (run.capacity_retry_count or 0) + 1
            retry_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=settings.capacity_retry_minutes)
            runstate.set_fields(run_name, {
                "capacity_retry_count": retry_count,
                "next_capacity_retry_at": retry_at,
            })
            frappe.db.commit()
            runlog.append(run_name, f"capacity retry {retry_count} failed, retry at {retry_at}")
            return {"retried": True, "deployed": False}
        _release_items(reserved_names)
        runstate.set_fields(run_name, {"status": STATUS_DEPLOY_FAILED, "status_reason": str(exc)[:140]})
        frappe.db.commit()
        runlog.append(run_name, f"capacity retry: deploy failed: {exc}")
        return {"retried": True, "deployed": False}
    except Exception as exc:
        _release_items(reserved_names)
        runstate.set_fields(run_name, {"status": STATUS_DEPLOY_FAILED, "status_reason": str(exc)[:140]})
        frappe.db.commit()
        runlog.append(run_name, f"capacity retry: unexpected error: {exc}")
        return {"retried": True, "deployed": False}

    pod_id = pod.get("id") or pod.get("podId")
    hourly_rate = pod.get("costPerHr") or settings.fallback_hourly_rate_usd
    ok = runstate.transition_if_status(run_name, STATUS_DEPLOYING, STATUS_STARTING, {
        "pod_id": pod_id,
        "pod_name": run_name,
        "hourly_rate": hourly_rate,
        "pod_created_at": frappe.utils.now_datetime(),
    })
    frappe.db.commit()
    if ok:
        runlog.append(run_name, f"capacity retry: pod {pod_id} created")
    return {"retried": True, "deployed": ok, "pod_id": pod_id}