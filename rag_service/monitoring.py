# rag_service/monitoring.py
#
# Identical in structure to tap_lms/monitoring.py.
# Kept as a separate module so rag_service has no import dependency on tap_lms.

# Important: refer to ../docs/log-config.md file for more details

import json
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

import frappe
from frappe.utils import now_datetime

_LOG_FILE = "rag_structured.log"


def _get_dynamic_log_path() -> str:
    """
    Safely resolves the absolute path to the logs directory across
    any development, staging, or production server layout.
    """
    # 1. Preferred: derive the bench root from frappe.local.sites_path, which
    # Frappe sets during frappe.init() to the *real* `<bench>/sites` directory.
    # This is reliable regardless of how tap_lms was installed (symlinked into
    # apps/tap_lms, or pip-installed editable straight from a bind-mounted
    # source dir, as in local podman setup)
    try:
        sites_path = getattr(frappe.local, "sites_path", None)
        if sites_path:
            bench_path = os.path.dirname(os.path.abspath(sites_path))
            if bench_path and bench_path.strip():
                return os.path.join(bench_path, "logs", _LOG_FILE)
    except Exception as e:
        print(f"Hit exception in generate log path (sites_path) ${e}")

    # 2. Fallback: frappe.utils.get_bench_path().
    try:
        bench_path = frappe.utils.get_bench_path()
        if bench_path and bench_path.strip():
            return os.path.join(bench_path, "logs", _LOG_FILE)
    except Exception as e:
        print(f"Hit exception in generate log path (get_bench_path) ${e}")

    # 3. Last resort: always-writable temp location.
    return f"/tmp/{_LOG_FILE}"


def _get_configured_logger() -> logging.Logger:
    """
    Dynamically configures and returns the logger using a live path.
    """
    logger = logging.getLogger("rag_structured_logger")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't also hand records to the root logger

    # Resolve the path LIVE right now
    log_file_path = _get_dynamic_log_path()

    # If the handler already matches this path, don't re-add it
    if logger.handlers:
        # Check if the existing file handler is pointing to the right spot
        existing_handler = logger.handlers[0]
        if isinstance(
            existing_handler, logging.FileHandler
        ) and existing_handler.baseFilename == os.path.abspath(log_file_path):
            return logger
        # If it's pointing to a broken path (like /logs), clear it out
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    try:
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,  # keep 5 rotated files (~50 MB total)
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(file_handler)
    except Exception as e:
        print(
            f"[monitoring] file handler setup failed for {log_file_path}: {e}",
            file=sys.stderr,
        )
        # Emergency fallback to standard error stream if container permissions reject disk operations
        if not logger.handlers:
            stream_handler = logging.StreamHandler(sys.stderr)
            logger.addHandler(stream_handler)

    return logger


def emit_structured_log(severity: str, message: str, **kwargs) -> None:
    """
    Emit a single JSON log line to stdout.

    All kwargs are included as top-level fields in the JSON object and
    become queryable in GCP Cloud Logging.

    Args:
        severity: "INFO" | "WARNING" | "ERROR" | "CRITICAL"
        message:  short machine-readable event name e.g. "submission_published"
        **kwargs: any additional fields (submission_id, duration_ms, etc.)
    """
    try:
        request_id = None

        # Pull request tracking ID out of the active execution thread
        # this requires nginx config change to append the request id
        # to the request header:
        #   /home/gcp-data/frappe-bench/config/nginx.conf
        #   proxy_set_header X-Request-Id $request_id;
        # and a config change in /etc/supervisor/conf.d/frappe-bench.conf
        # to get the request_id in the header:
        #   command=/home/gcp-data/frappe-bench/env/bin/gunicorn -b 127.0.0.1:8000 -w 5 --max-requests 5000 --max-requests-jitter 500 -t 120 --graceful-timeout 30 frappe.app:application --preload --capture-output --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" [Request-ID: %{REQUEST_ID}e]'

        if hasattr(frappe.local, "request") and frappe.local.request:
            # 1. Check if Frappe/Werkzeug automatically parsed and assigned it
            request_id = getattr(frappe.local.request, "unique_id", None)

            # 2. Fallback: Search the raw WSGI environment keys directly
            if not request_id and hasattr(frappe.local.request, "environ"):
                env = frappe.local.request.environ
                request_id = (
                    env.get(
                        "HTTP_X_REQUEST_ID"
                    )  # Standard Nginx proxy header translation
                    or env.get(
                        "REQUEST_ID"
                    )  # Raw Gunicorn internal environment variable
                    or env.get(
                        "HTTP_X_CORRELATION_ID"
                    )  # Common 3rd party vendor header variation
                )

        payload = {
            "severity": severity.upper(),
            "message": message,
            "timestamp": now_datetime().isoformat(),
            "request_id": request_id,
            "app": "rag_service",
            "app_env": os.getenv("APP_ENV", "unknown"),
        }

        # Merge dynamic keyword parameters
        payload.update({k: v for k, v in kwargs.items() if v is not None})

        # Automatic exception tracing for error states
        if severity.upper() in ("ERROR", "CRITICAL") and sys.exc_info()[0] is not None:
            payload["exception"] = traceback.format_exc()

        # Emit 100% clean JSON string
        gcp_logger = _get_configured_logger()
        gcp_logger.info(json.dumps(payload, ensure_ascii=False))

    except Exception as e:
        # Fallback to stderr if the disk write stream encounters an OS failure
        sys.stderr.write(f"\n[STRUCTURED_LOG_FAIL] {str(e)}\n")
        sys.stderr.flush()


def emit(severity: str, message: str, **kwargs) -> None:
    try:
        emit_structured_log(severity=severity, message=message, **kwargs)
    except Exception as e:
        try:
            frappe.logger().info(f"[{severity}] {message} {kwargs}")
        except Exception:
            pass
        print(f"[monitoring] _emit failed for '{message}': {e}", flush=True)


def record_request(path, method, status_code, duration_ms, user=None):
    emit(
        severity="INFO" if status_code < 400 else "ERROR",
        message="http_request",
        http_path=path,
        http_method=method,
        http_status=status_code,
        duration_ms=round(duration_ms, 2),
        user=user,
    )


def record_job(job_name, status, duration_ms=None, error=None, **extra):
    emit(
        severity="INFO" if status in ("success", "skip") else "ERROR",
        message="background_job",
        job_name=job_name,
        job_status=status,
        duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        error=str(error) if error else None,
        **extra,
    )


# ── RAG-specific pipeline events ──────────────────────────────────────────────


def record_rag_submission_received(submission_id, student_id=None, assignment_id=None):
    emit(
        severity="INFO",
        message="rag_submission_received",
        submission_id=submission_id,
        student_id=student_id,
        assignment_id=assignment_id,
    )


def record_rag_feedback_complete(
    submission_id, model_used=None, template_used=None, duration_ms=None
):
    emit(
        severity="INFO",
        message="rag_feedback_complete",
        submission_id=submission_id,
        model_used=model_used,
        template_used=template_used,
        duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
    )


def record_rag_feedback_failed(submission_id, error, duration_ms=None):
    emit(
        severity="ERROR",
        message="rag_feedback_failed",
        submission_id=submission_id,
        error=str(error),
        duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
    )


def record_llm_call(
    submission_id, provider, model, status, duration_ms=None, error=None
):
    emit(
        severity="INFO" if status == "success" else "ERROR",
        message="llm_call_complete" if status == "success" else "llm_call_failed",
        submission_id=submission_id,
        llm_provider=provider,
        llm_model=model,
        duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        error=str(error) if error else None,
    )


def record_tap_lms_api_call(
    submission_id, endpoint, duration_ms, cache_hit=False, status_code=None
):
    emit(
        severity="INFO" if not status_code or status_code < 400 else "ERROR",
        message="tap_lms_api_call",
        submission_id=submission_id,
        endpoint=endpoint,
        duration_ms=round(duration_ms, 2),
        cache_hit=cache_hit,
        http_status=status_code,
    )
