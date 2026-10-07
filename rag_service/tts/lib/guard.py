import frappe

from ..constants import BODY_SIZE_CAP_BYTES, RUN_DOCTYPE
from . import runstate, tokens

GENERIC_AUTH_ERROR = "unauthorized"

RATE_LIMITS = {
    "progress": (120, 60),
    "beat": (30, 60),
    "manifest": (10, 60),
    "complete": (10, 60),
}

BAD_AUTH_WINDOW_SECONDS = 300
BAD_AUTH_MAX = 20


class GuardStop(Exception):
    def __init__(self, http_code, message=GENERIC_AUTH_ERROR):
        super().__init__(message)
        self.http_code = http_code
        self.message = message


def _client_ip():
    return frappe.local.request_ip or "unknown"


def _incr_with_ttl(cache_key, window_seconds):
    cache = frappe.cache()
    current = cache.get_value(cache_key)
    count = (int(current) if current else 0) + 1
    cache.set_value(cache_key, count, expires_in_sec=window_seconds)
    return count


def _check_rate_limit(endpoint, key):
    limit, window = RATE_LIMITS.get(endpoint, (60, 60))
    cache_key = f"tts:rate:{endpoint}:{key}"
    count = _incr_with_ttl(cache_key, window)
    if count > limit:
        raise GuardStop(429, "rate_limited")


def _check_body_size():
    content_length = frappe.request.content_length or 0
    if content_length > BODY_SIZE_CAP_BYTES:
        raise GuardStop(413, "body_too_large")


def _bad_auth_throttle(ip):
    cache_key = f"tts:badauth:{ip}"
    count = _incr_with_ttl(cache_key, BAD_AUTH_WINDOW_SECONDS)
    if count > BAD_AUTH_MAX:
        raise GuardStop(429, "rate_limited")


def enter(endpoint):
    if frappe.request.method != "POST":
        raise GuardStop(405, "post_only")

    _check_body_size()

    run_id = frappe.get_request_header("X-Run-Id")
    token = frappe.get_request_header("X-Run-Token")
    ip = _client_ip()

    if not run_id or not token:
        _bad_auth_throttle(ip)
        raise GuardStop(401, GENERIC_AUTH_ERROR)

    from .settings import load as load_settings
    try:
        secret = load_settings().get_secret("tts_pod_shared_secret")
        tokens.verify(run_id, token, secret)
    except Exception:
        _bad_auth_throttle(ip)
        raise GuardStop(401, GENERIC_AUTH_ERROR)

    _check_rate_limit(endpoint, run_id)

    if not frappe.db.exists(RUN_DOCTYPE, run_id):
        raise GuardStop(401, GENERIC_AUTH_ERROR)

    run = runstate.get_for_update(run_id)
    if run is None:
        raise GuardStop(401, GENERIC_AUTH_ERROR)

    if runstate.is_terminal(run.status):
        raise GuardStop(409, "run_terminal")

    return run