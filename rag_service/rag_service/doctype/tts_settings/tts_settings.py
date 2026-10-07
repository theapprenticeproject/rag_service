import re

import frappe
from frappe.model.document import Document

DEFAULT_BASE_URL = "https://api.kaapi.ai"
DEFAULT_RUNPOD_API_BASE = "https://rest.runpod.io/v1"

CRON_FIELD_RANGES = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 7),
)


def get_tts_settings():
    return frappe.get_single("TTS Settings")


def tts_enabled() -> bool:
    return bool(get_tts_settings().enabled)


def _validate_cron_field(field_text, lo, hi):
    if field_text == "*":
        return True
    for part in field_text.split(","):
        part = part.strip()
        if not part:
            return False
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                return False
            step = int(step_text)
        if part == "*":
            continue
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2 or not all(b.isdigit() for b in bounds):
                return False
            start, end = int(bounds[0]), int(bounds[1])
            if start < lo or end > hi or start > end:
                return False
        else:
            if not part.isdigit():
                return False
            value = int(part)
            if value < lo or value > hi:
                return False
    return True


def validate_cron_expression(expression):
    fields = expression.strip().split()
    if len(fields) != 5:
        return False
    return all(
        _validate_cron_field(field, lo, hi)
        for field, (lo, hi) in zip(fields, CRON_FIELD_RANGES)
    )


def _field_matches(field_text, value, lo, hi):
    if field_text == "*":
        return True
    for part in field_text.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            bounds = part.split("-")
            start, end = int(bounds[0]), int(bounds[1])
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _dow_matches(field_text, weekday):
    if _field_matches(field_text, weekday, 0, 7):
        return True
    if weekday == 0 and _field_matches(field_text, 7, 0, 7):
        return True
    return False


def cron_matches_now(expression, now):
    fields = expression.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    weekday = now.isoweekday() % 7
    return (
        _field_matches(minute, now.minute, 0, 59)
        and _field_matches(hour, now.hour, 0, 23)
        and _field_matches(dom, now.day, 1, 31)
        and _field_matches(month, now.month, 1, 12)
        and _dow_matches(dow, weekday)
    )


class TTSSettings(Document):
    def validate(self):
        self.runpod_api_base = (self.runpod_api_base or DEFAULT_RUNPOD_API_BASE).strip().rstrip("/")
        if not self.runpod_api_base.startswith(("http://", "https://")):
            self.runpod_api_base = "https://" + self.runpod_api_base

        if self.public_base_url and not self.public_base_url.startswith("https://"):
            frappe.throw("Public Base URL must use HTTPS.")

        if (self.max_chars_per_text or 0) > 300:
            frappe.throw("Max Chars Per Text cannot exceed 300 (pod-side hard limit).")

        if (self.min_items_to_deploy or 0) > (self.max_clips_per_run or 0):
            frappe.throw("Min Items To Deploy must be <= Max Clips Per Run.")

        if (self.max_cost_per_run_usd or 0) > (self.max_cost_per_day_usd or 0):
            frappe.throw("Max Cost Per Run must be <= Max Cost Per Day.")
        if (self.max_cost_per_day_usd or 0) > (self.max_cost_per_month_usd or 0):
            frappe.throw("Max Cost Per Day must be <= Max Cost Per Month.")

        if (self.max_consecutive_bad_runs or 0) < 1:
            frappe.throw("Max Consecutive Bad Runs must be at least 1.")
        if not (0 <= (self.bad_run_success_floor_percent or 0) <= 100):
            frappe.throw("Bad Run Success Floor Percent must be between 0 and 100.")

        self._validate_scheduling()

        name = (self.image_name or "").strip().lower()
        if name and "@sha256:" not in name:
            last = name.rsplit("/", 1)[-1]
            tag = last.split(":", 1)[1] if ":" in last else ""
            if tag in ("", "stable", "latest"):
                frappe.throw(
                    "Image Name needs an immutable tag (a short SHA) or a @sha256 digest, "
                    "not 'stable', 'latest', or no tag."
                )

        if self.enabled:
            if not self.get_secret("runpod_api_key"):
                frappe.throw("RunPod API Key is required to enable TTS batches.")
            if not self.get_secret("tts_pod_shared_secret"):
                frappe.throw("Pod Shared Secret is required to enable TTS batches.")
            if not self.get_secret("tts_gcs_service_account_json_b64"):
                frappe.throw("GCS Service Account JSON is required to enable TTS batches.")
            if not (self.public_base_url or "").strip():
                frappe.throw("Public Base URL is required to enable TTS batches.")
            if not (self.gpu_type_ids or "").strip():
                frappe.throw("GPU Type Ids cannot be empty.")
            if not (self.image_name or "").strip():
                frappe.throw("Image Name is required to enable TTS batches.")
            if not (self.model_revision or "").strip():
                frappe.throw("Model Revision is required to enable TTS batches.")
            if not (self.alert_email or "").strip():
                frappe.throw("Alert Email is required to enable TTS batches.")

    def _validate_scheduling(self):
        if not self.scheduling_enabled:
            return

        self.schedule_mode = self.schedule_mode or "Interval"

        if self.schedule_mode == "Interval":
            if (self.schedule_interval_minutes or 0) < 5:
                frappe.throw("Schedule Interval Minutes must be at least 5.")
        elif self.schedule_mode == "Cron":
            lines = [
                line.strip()
                for line in (self.schedule_cron_expressions or "").splitlines()
                if line.strip()
            ]
            if not lines:
                frappe.throw("Enter at least one cron expression, one per line.")
            if len(lines) > 20:
                frappe.throw("No more than 20 cron expressions are supported.")
            bad = [line for line in lines if not validate_cron_expression(line)]
            if bad:
                frappe.throw(
                    "Invalid cron expression(s): " + "; ".join(bad)
                    + ". Use standard 5-field cron syntax, e.g. '0 2 * * *'."
                )
        else:
            frappe.throw("Schedule Mode must be Interval or Cron.")

        if (self.capacity_retry_minutes or 0) < 2:
            frappe.throw("Capacity Retry Interval Minutes must be at least 2.")

    def get_secret(self, fieldname):
        if not self.get(fieldname):
            return ""
        return (self.get_password(fieldname, raise_exception=False) or "").strip()