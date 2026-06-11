# rag_service/monitoring.py
#
# Identical in structure to tap_lms/monitoring.py.
# Kept as a separate module so rag_service has no import dependency on tap_lms.

import json
import frappe
from frappe.utils import now_datetime


def emit_structured_log(severity: str, message: str, **kwargs) -> None:
    try:
        import os
        payload = {
            "severity": severity,
            "message": message,
            "timestamp": str(now_datetime()),
            "app": "rag_service",
            "app_env": os.environ.get("APP_ENV", "unknown"),
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass


def record_request(path, method, status_code, duration_ms, user=None):
    try:
        emit_structured_log(
            severity="INFO" if status_code < 400 else "ERROR",
            message="http_request",
            http_path=path,
            http_method=method,
            http_status=status_code,
            duration_ms=round(duration_ms, 2),
            user=user,
        )
    except Exception:
        pass


def record_job(job_name, status, duration_ms=None, error=None, **extra):
    try:
        emit_structured_log(
            severity="INFO" if status in ("success", "skip") else "ERROR",
            message="background_job",
            job_name=job_name,
            job_status=status,
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
            error=str(error) if error else None,
            **extra,
        )
    except Exception:
        pass


# ── RAG-specific pipeline events ──────────────────────────────────────────────

def record_rag_submission_received(submission_id, student_id=None, assignment_id=None):
    try:
        emit_structured_log(
            severity="INFO",
            message="rag_submission_received",
            submission_id=submission_id,
            student_id=student_id,
            assignment_id=assignment_id,
        )
    except Exception:
        pass


def record_rag_feedback_complete(submission_id, model_used=None, template_used=None, duration_ms=None):
    try:
        emit_structured_log(
            severity="INFO",
            message="rag_feedback_complete",
            submission_id=submission_id,
            model_used=model_used,
            template_used=template_used,
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        )
    except Exception:
        pass


def record_rag_feedback_failed(submission_id, error, duration_ms=None):
    try:
        emit_structured_log(
            severity="ERROR",
            message="rag_feedback_failed",
            submission_id=submission_id,
            error=str(error),
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        )
    except Exception:
        pass


def record_llm_call(submission_id, provider, model, status, duration_ms=None, error=None):
    try:
        emit_structured_log(
            severity="INFO" if status == "success" else "ERROR",
            message="llm_call_complete" if status == "success" else "llm_call_failed",
            submission_id=submission_id,
            llm_provider=provider,
            llm_model=model,
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
            error=str(error) if error else None,
        )
    except Exception:
        pass


def record_tap_lms_api_call(submission_id, endpoint, duration_ms, cache_hit=False, status_code=None):
    try:
        emit_structured_log(
            severity="INFO" if not status_code or status_code < 400 else "ERROR",
            message="tap_lms_api_call",
            submission_id=submission_id,
            endpoint=endpoint,
            duration_ms=round(duration_ms, 2),
            cache_hit=cache_hit,
            http_status=status_code,
        )
    except Exception:
        pass
