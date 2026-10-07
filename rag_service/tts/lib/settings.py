import frappe

from ...rag_service.doctype.tts_settings.tts_settings import get_tts_settings

SNAPSHOT_FIELDS = (
    "enabled", "allow_force_start", "min_items_to_deploy", "max_clips_per_run",
    "max_chars_per_text", "over_length_policy", "supported_languages",
    "fallback_hourly_rate_usd", "max_cost_per_run_usd", "max_cost_per_day_usd",
    "max_cost_per_month_usd", "estimate_safety_factor", "est_startup_seconds",
    "est_seconds_per_clip", "pod_limit_max_seconds", "startup_deadline_minutes",
    "heartbeat_timeout_minutes", "token_buffer_minutes", "termination_check_delay_seconds",
    "max_consecutive_bad_runs", "bad_run_success_floor_percent", "alert_email",
    "runpod_api_base", "image_name", "gpu_type_ids", "gpu_count", "container_disk_gb",
    "http_timeout_seconds", "public_base_url", "api_module_path", "gcs_bucket",
    "gcs_prefix", "cdn_base_url", "model_revision", "verify_urls_on_write",
)

_LIST_FIELDS = ("gpu_type_ids",)
MAX_URL_LENGTH = 180


def _split_lines(value):
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


class Settings:
    def __init__(self, doc):
        for field in SNAPSHOT_FIELDS:
            setattr(self, field, doc.get(field))
        self.public_base_url = (self.public_base_url or "").rstrip("/")
        self.cdn_base_url = (self.cdn_base_url or "").rstrip("/")
        self.gcs_prefix = (self.gcs_prefix or "tts").strip("/")
        for field in _LIST_FIELDS:
            value = getattr(self, field)
            if isinstance(value, str):
                setattr(self, field, _split_lines(value))
            elif value is None:
                setattr(self, field, [])
        self.est_startup_seconds = int(self.est_startup_seconds or 180)
        self.est_seconds_per_clip = float(self.est_seconds_per_clip or 1.0)
        self.estimate_safety_factor = float(self.estimate_safety_factor or 1.3)
        self.max_consecutive_bad_runs = int(self.max_consecutive_bad_runs or 2)
        self.bad_run_success_floor_percent = int(self.bad_run_success_floor_percent or 20)

    def kill_switch_on(self) -> bool:
        return bool(self.enabled)

    def max_text_chars(self) -> int:
        return int(self.max_chars_per_text or 300)

    def max_url_length(self) -> int:
        return MAX_URL_LENGTH

    def snapshot(self) -> dict:
        out = {}
        for field in SNAPSHOT_FIELDS:
            value = getattr(self, field)
            out[field] = "\n".join(value) if isinstance(value, (list, tuple)) else value
        return out

    def get_secret(self, fieldname):
        doc = get_tts_settings()
        return doc.get_secret(fieldname)


def load() -> Settings:
    return Settings(get_tts_settings())


def load_fresh() -> Settings:
    frappe.clear_cache(doctype="TTS Settings")
    from . import languages
    languages.reset_cache()
    return Settings(get_tts_settings())


def api_path_prefix(settings):
    module_path = (settings.api_module_path or "rag_service.tts.api.pod").strip(".")
    return f"/api/method/{module_path}."
