import hashlib
import hmac
import time


class TokenInvalid(Exception):
    pass


def _mac(run_id, expiry, secret):
    payload = f"{run_id}|{expiry}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def issue(run_id, secret, ttl_minutes):
    expiry = int(time.time()) + ttl_minutes * 60
    signature = _mac(run_id, expiry, secret)
    return f"{expiry}.{signature}"


def verify(run_id, token, secret):
    if not token or "." not in token:
        raise TokenInvalid("malformed")
    expiry_text, _, signature = token.partition(".")
    try:
        expiry = int(expiry_text)
    except ValueError as exc:
        raise TokenInvalid("malformed") from exc
    expected = _mac(run_id, expiry, secret)
    if not hmac.compare_digest(expected, signature):
        raise TokenInvalid("bad_signature")
    if time.time() > expiry:
        raise TokenInvalid("expired")
    return True