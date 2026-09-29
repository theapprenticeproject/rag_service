"""Copy each Kaapi Webhook Log's raw body to GCS as a .txt file.

Outgoing (submission) bodies go to  kaapi_logs/kaapi_logs_request_*.txt
Incoming (webhook) bodies go to     kaapi_logs/kaapi_logs_response_*.txt
in the assignment_submission_tap bucket. Objects are stored with
`Content-Disposition: attachment`, so opening the saved
https://storage.cloud.google.com/... URL (as a signed-in Google user with read access)
downloads the file instead of rendering it in the browser.

The upload runs as a background job keyed by log name, so neither the Kaapi submit nor
the webhook response waits on GCS, and a GCS failure never affects grading. The job reads
the already-redacted raw_body from the committed log and records the object URL back on it.
"""
import frappe

BUCKET = "assignment_submission_tap"
FOLDER = "kaapi_logs"
PREFIXES = {"Outgoing": "kaapi_logs_request", "Incoming": "kaapi_logs_response"}
URL_PREFIX = "https://storage.cloud.google.com"


def enqueue_raw_body_upload(log_name: str) -> None:
    """Queue the GCS upload for a committed log. Never raises."""
    try:
        frappe.enqueue(
            "rag_service.core.kaapi_log_archive.upload_raw_body",
            queue="short",
            job_name=f"kaapi-log-upload-{log_name}",
            log_name=log_name,
        )
    except Exception as e:
        frappe.log_error(f"Could not enqueue GCS upload for Kaapi Webhook Log {log_name}: {e}",
                         "Kaapi Webhook Log Error")


def object_name(log_name: str, direction: str, logged_at) -> str:
    stamp = frappe.utils.get_datetime(logged_at).strftime("%Y%m%d_%H%M%S")
    return f"{FOLDER}/{PREFIXES[direction]}_{stamp}_{log_name}.txt"


def upload_raw_body(log_name: str) -> str:
    """Background job: write the log's raw_body to GCS as a downloadable attachment and
    store its storage.cloud.google.com URL on the log."""
    from ..utils.gcp_service_client import GCPServiceClient

    log = frappe.db.get_value("Kaapi Webhook Log", log_name,
                              ["direction", "received_at", "raw_body"], as_dict=True)
    if not log:
        raise ValueError(f"Kaapi Webhook Log {log_name} not found")

    name = object_name(log_name, log.direction, log.received_at)
    blob = GCPServiceClient().client.bucket(BUCKET).blob(name)
    blob.content_disposition = f'attachment; filename="{name.rsplit("/", 1)[-1]}"'
    blob.upload_from_string((log.raw_body or "").encode("utf-8"),
                            content_type="text/plain; charset=utf-8")

    url = f"{URL_PREFIX}/{BUCKET}/{name}"
    frappe.db.set_value("Kaapi Webhook Log", log_name, "raw_body_gcs_url", url)
    frappe.db.commit()
    return url
