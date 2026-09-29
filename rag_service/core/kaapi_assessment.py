# rag_service/rag_service/core/kaapi_assessment.py
"""
Kaapi ASSESSMENT API client — the SUBMIT side of the two-phase grading flow.

Phase 1 (this module): pack rendered prompts (+ image URLs) into a batch and
`POST /assessments` with our `callback_url`. Kaapi grades in parallel and POSTs the
results to our webhook (phase 2, see `api/kaapi_webhook.py`).

Why it's built this way (verified against the live API, see KAAPI_ASSESSMENT_SWITCH_SCOPE.md):
- There is NO poll/GET endpoint for assessments — results are webhook-only, so a
  `callback_url` is mandatory.
- Results come back POSITIONALLY (`items[i]` <-> `input.data[i]`); a failed row returns
  `output.assessment = null` and echoes NO id. So each row carries a `submission_id`
  (the Feedback Request name) for validation, and we persist its batch index as the
  primary correlation key.
- One thin generic config per media type (image/text) carries the fixed grading
  instructions + output schema; the rubric/description/level/language ride inside the
  `rendered_prompt` input, so a single config serves every assignment.

Configuration and encrypted credentials come from the site's Kaapi Settings singleton.
No environment variables or LLM Settings record are required for assessments.
"""
import json
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import frappe
import requests

from ..rag_service.doctype.kaapi_settings.kaapi_settings import DEFAULT_BASE_URL, get_kaapi_settings
from .kaapi_log_archive import enqueue_raw_body_upload

API_PREFIX = "/api/v1"


class KaapiAssessmentClient:
    TIMEOUT_SECONDS = 60

    def __init__(self):
        self.settings = get_kaapi_settings()
        self.api_key = self.settings.get_secret("api_key")
        if not self.api_key:
            raise ValueError("Set API Key in Kaapi Settings.")
        self.base_url = self._normalize_base(self.settings.base_url or DEFAULT_BASE_URL)
        self.callback_url = self.settings.get_callback_url()

    # ---- config lookup ----
    def _config_for(self, media_type: str) -> Tuple[Optional[str], int]:
        kind = "text" if media_type == "text" else "image"
        config_id = (self.settings.get(f"{kind}_config_id") or "").strip() or None
        version = int(self.settings.get(f"{kind}_config_version") or 1)
        return config_id, version

    # ---- http ----
    @staticmethod
    def _normalize_base(url: str) -> str:
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _headers(self) -> Dict:
        key = self.api_key.strip()
        value = key if key.lower().startswith("apikey ") else f"ApiKey {key}"
        return {
            "X-API-KEY": value,
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    def _unwrap(self, body: Dict) -> Dict:
        if isinstance(body, dict) and "success" in body and "data" in body:
            if not body.get("success", True):
                raise Exception(f"Kaapi error: {body.get('error') or body.get('errors')}")
            return body["data"]
        return body

    def _post(self, path: str, payload: Dict) -> Dict:
        r = requests.post(
            f"{self.base_url}{API_PREFIX}{path}",
            json=payload,
            headers=self._headers(),
            timeout=self.TIMEOUT_SECONDS,
        )
        if not r.ok:
            raise Exception(f"Kaapi POST {path} -> {r.status_code}: {r.text[:400]}")
        return self._unwrap(r.json())

    def _log_submission(self, body: Dict) -> str:
        """Persist the whole batch before sending it, with the callback token redacted."""
        callback = urlsplit(body["callback_url"])
        query = [(key, "***" if key == "token" else value)
                 for key, value in parse_qsl(callback.query, keep_blank_values=True)]
        logged_body = {
            **body,
            "callback_url": urlunsplit(callback._replace(query=urlencode(query))),
        }
        log = frappe.get_doc({
            "doctype": "Kaapi Webhook Log",
            "direction": "Outgoing",
            "received_at": frappe.utils.now_datetime(),
            "processing_status": "Submitting",
            "request_url": f"{self.base_url}{API_PREFIX}/assessments",
            "content_type": "application/json",
            "raw_body": json.dumps(logged_body, indent=2, ensure_ascii=False),
        })
        log.insert(ignore_permissions=True)
        # Keep the payload even if the HTTP request fails or subsequent work rolls back.
        frappe.db.commit()
        enqueue_raw_body_upload(log.name)
        return log.name

    @staticmethod
    def _update_submission_log(log_name: str, status: str, result: Dict = None,
                               error: str = None) -> None:
        try:
            frappe.db.set_value("Kaapi Webhook Log", log_name, {
                "processing_status": status,
                "assessment_id": (result or {}).get("assessment_id"),
                "result": json.dumps(result, indent=2, ensure_ascii=False) if result is not None else None,
                "error": error,
            })
            frappe.db.commit()
        except Exception as e:
            # A logging failure must not make an accepted batch look like a failed submit.
            frappe.db.rollback()
            frappe.log_error(f"Could not update Kaapi Webhook Log {log_name}: {e}",
                             "Kaapi Webhook Log Error")

    @staticmethod
    def _normalize_image_url(image_url: Optional[str]) -> str:
        """Return a GCS URI or reject an image that Kaapi cannot read through GCS."""
        error = "Kaapi image URL must be gs://bucket/object or a supported GCS HTTP(S) URL."
        if not isinstance(image_url, str) or not image_url.strip():
            raise ValueError(error)
        parsed = urlsplit(image_url.strip())
        if parsed.username is not None or parsed.password is not None or parsed.port is not None:
            raise ValueError(error)

        host = parsed.hostname or ""
        if parsed.scheme == "gs":
            if parsed.query or parsed.fragment:
                raise ValueError(error)
            bucket, object_name = parsed.netloc, parsed.path.removeprefix("/")
        elif parsed.scheme not in ("http", "https"):
            raise ValueError(error)
        elif host in ("storage.googleapis.com", "storage.cloud.google.com"):
            bucket, _, object_name = parsed.path.removeprefix("/").partition("/")
        elif host.endswith(".storage.googleapis.com"):
            bucket = host.removesuffix(".storage.googleapis.com")
            object_name = parsed.path.removeprefix("/")
        else:
            raise ValueError(error)

        if not bucket or not object_name or any(char.isspace() for char in bucket):
            raise ValueError(error)
        # Preserve path escaping; HTTPS signatures/query parameters are not object names.
        return f"gs://{bucket}/{object_name}"

    # ---- public ----
    @classmethod
    def build_row(cls, submission_id: str, rendered_prompt: str,
                  media_type: str, image_url: Optional[str] = None) -> Dict:
        """One `input.data` row shaped to the config's input_schema."""
        row = {"submission_id": submission_id, "rendered_prompt": rendered_prompt}
        if media_type != "text":
            row["artwork_image"] = cls._normalize_image_url(image_url)
        return row

    def submit(self, rows: List[Dict], media_type: str,
               request_metadata: Optional[Dict] = None) -> str:
        """Submit one batch to Kaapi. Returns the batch `assessment_id`.

        `rows` must be in the order we want to correlate by — the row at index i
        becomes `input.data[i]` and its result comes back as `items[i]`.
        """
        if not rows:
            raise ValueError("submit() called with no rows")
        config_id, version = self._config_for(media_type)
        if not config_id:
            raise ValueError(
                f"No Kaapi config configured for media_type={media_type} "
                f"(set {'Text' if media_type == 'text' else 'Image'} Config ID in Kaapi Settings)"
            )

        if media_type != "text":
            # Normalize at the send boundary, including callers that construct rows directly.
            rows = [
                {**row, "artwork_image": self._normalize_image_url(row.get("artwork_image"))}
                for row in rows
            ]

        body = {
            "config": {"id": config_id, "version": version},
            "input": {"data": rows},
            "callback_url": self.callback_url,
            "request_metadata": request_metadata or {},
        }
        log_name = self._log_submission(body)
        try:
            data = self._post("/assessments", body)
            assessment_id = data.get("assessment_id")
            if not assessment_id:
                raise Exception(f"Kaapi /assessments returned no assessment_id: {data}")
        except Exception as e:
            self._update_submission_log(log_name, "Error", error=str(e))
            raise
        self._update_submission_log(log_name, "Submitted", result=data)
        return assessment_id
