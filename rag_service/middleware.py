# rag_service/middleware.py — identical pattern to tap_lms

import time
import traceback

import frappe

from .monitoring import _emit, record_request


def before_request():
    try:
        frappe.local._sre_t0 = time.monotonic()
    except Exception:
        pass


def after_request():
    try:
        t0 = getattr(frappe.local, "_sre_t0", None)
        duration_ms = (time.monotonic() - t0) * 1000 if t0 is not None else None
        req = getattr(frappe.local, "request", None)
        response = getattr(frappe.local, "response", None)
        status_code = (
            response.get("http_status_code", 200) if isinstance(response, dict) else 200
        )
        record_request(
            path=req.path if req else "unknown",
            method=req.method if req else "unknown",
            status_code=int(status_code),
            duration_ms=duration_ms,
        )
    except Exception:
        pass


def on_exception():
    req = getattr(frappe.local, "request", None)
    _emit(
        severity="ERROR",
        message="unhandled_exception",
        path=req.path if req else "unknown",
        traceback=traceback.format_exc(),
    )
