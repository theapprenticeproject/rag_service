import time

import frappe

from ..constants import RUN_DOCTYPE
from ..lib import runlog, runstate
from ..lib import settings as settings_lib
from ..lib.runpod import RunPodClient

MAX_ATTEMPTS = 4


def _client(settings):
    return RunPodClient(settings.runpod_api_base, settings.get_secret("runpod_api_key"), settings.http_timeout_seconds)


def verify_termination(run_name):
    settings = settings_lib.load()
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if not run.pod_id:
        runstate.set_fields(run_name, {"termination_status": "Not Needed"})
        frappe.db.commit()
        return {"termination_status": "Not Needed"}

    time.sleep(min(settings.termination_check_delay_seconds, 5))

    client = _client(settings)
    attempts = (run.termination_attempts or 0) + 1
    runstate.set_fields(run_name, {"termination_attempts": attempts})

    pod = client.get_pod(run.pod_id)
    if pod is None:
        runstate.set_fields(run_name, {"termination_status": "Verified", "terminated_at": frappe.utils.now_datetime()})
        frappe.db.commit()
        runlog.append(run_name, "termination verified: pod gone")
        return {"termination_status": "Verified"}

    try:
        client.delete_pod(run.pod_id)
    except Exception as exc:
        runlog.append(run_name, f"termination delete attempt {attempts} failed: {exc}")

    if attempts >= MAX_ATTEMPTS:
        runstate.set_fields(run_name, {"termination_status": "Manual Action Needed"})
        frappe.db.commit()
        if settings.alert_email:
            frappe.sendmail(
                recipients=[settings.alert_email],
                subject=f"TTS: pod {run.pod_id} could not be confirmed deleted",
                message=f"Run {run_name} pod {run.pod_id} needs manual termination in RunPod.",
            )
        runlog.append(run_name, "termination escalated: manual action needed")
        return {"termination_status": "Manual Action Needed"}

    runstate.set_fields(run_name, {"termination_status": "Pending"})
    frappe.db.commit()
    frappe.enqueue(
        "rag_service.tts.job.termination.verify_termination",
        queue="long", run_name=run_name, enqueue_after_commit=True,
    )
    return {"termination_status": "Pending"}


def force_delete_now(run_name):
    settings = settings_lib.load()
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if run.pod_id:
        client = _client(settings)
        try:
            client.delete_pod(run.pod_id)
        except Exception as exc:
            runlog.append(run_name, f"force_delete_now failed: {exc}")
    runstate.set_fields(run_name, {"termination_status": "Pending"})
    frappe.db.commit()
    return verify_termination(run_name)
