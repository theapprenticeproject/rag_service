# Copyright (c) 2026, TAP and contributors
# For license information, please see license.txt

from urllib.parse import parse_qsl, urlsplit

import frappe
from frappe.model.document import Document


DEFAULT_BASE_URL = "https://api.kaapi.ai"


def get_kaapi_settings():
    # Read per operation so workers and the web process use the same site settings.
    return frappe.get_single("Kaapi Settings")


def assessments_enabled() -> bool:
    return bool(get_kaapi_settings().enable_assessments)


class KaapiSettings(Document):
    def validate(self):
        self.base_url = (self.base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            self.base_url = "https://" + self.base_url
        self._validate_url(self.base_url, "Base URL", allow_query=False)

        self.callback_url = (self.callback_url or "").strip()
        if self.callback_url:
            self._validate_url(self.callback_url, "Callback URL")
            if any(key == "token" for key, _ in parse_qsl(urlsplit(self.callback_url).query)):
                frappe.throw("Enter Callback URL without a token; Kaapi authenticates with HMAC headers.")

        for kind in ("image", "text", "video"):
            self.set(f"{kind}_config_id", (self.get(f"{kind}_config_id") or "").strip())
            field = f"{kind}_config_version"
            version = self.get(field)
            if version in (None, ""):
                version = 1
            try:
                if int(version) < 1 or float(version) != int(version):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                frappe.throw(f"{kind.title()} Config Version must be a positive integer.")
            self.set(field, int(version))

        if self.enable_assessments:
            if not self.get_secret("api_key"):
                frappe.throw("API Key is required to enable Kaapi assessments.")
            if not self.callback_url:
                frappe.throw("Callback URL is required to enable Kaapi assessments.")
            if not self.allow_insecure_webhook and not self.get_secret("webhook_secret"):
                frappe.throw("Webhook Secret is required to enable Kaapi assessments.")
            if not (self.image_config_id or self.text_config_id or self.video_config_id):
                frappe.throw("Configure at least one Image, Text or Video Config ID for Kaapi assessments.")

    @staticmethod
    def _validate_url(value, label, allow_query=True):
        try:
            parts = urlsplit(value)
            valid = (
                parts.scheme in ("http", "https") and parts.hostname
                and not parts.username and not parts.password and not parts.fragment
                and (allow_query or not parts.query)
                and not any(char.isspace() for char in value)
            )
            parts.port  # Validate a port if one is supplied.
        except ValueError:
            valid = False
        if not valid:
            frappe.throw(f"{label} must be an absolute HTTP(S) URL without credentials or a fragment.")

    def get_secret(self, fieldname):
        if not self.get(fieldname):
            return ""
        return (self.get_password(fieldname, raise_exception=False) or "").strip()

    def get_callback_url(self):
        """Return the bare callback URL; Kaapi authenticates callbacks with HMAC headers."""
        callback_url = (self.callback_url or "").strip()
        if not callback_url:
            raise ValueError("Set Callback URL in Kaapi Settings; assessments require a webhook.")
        secret = self.get_secret("webhook_secret")
        if not secret and not self.allow_insecure_webhook:
            raise ValueError("Set Webhook Secret in Kaapi Settings.")
        return callback_url
