import frappe
from frappe.model.document import Document

DEFAULT_BASE_URL = "https://api.kaapi.ai"
DEFAULT_RUNPOD_API_BASE = "https://rest.runpod.io/v1"


def get_tts_settings():
    return frappe.get_single("TTS Settings")


def tts_enabled() -> bool:
    return bool(get_tts_settings().enabled)


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

    def get_secret(self, fieldname):
        if not self.get(fieldname):
            return ""
        return (self.get_password(fieldname, raise_exception=False) or "").strip()