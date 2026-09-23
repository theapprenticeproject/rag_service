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

Config/creds resolution (env first, LLM Settings fallback for the key):
  KAAPI_API_KEY                 (else LLM Settings provider="Kaapi" api_secret)
  KAAPI_BASE_URL                (default https://api.kaapi.ai)
  KAAPI_CALLBACK_URL            (the public rag_service webhook URL)
  KAAPI_IMAGE_CONFIG_ID / _VERSION
  KAAPI_TEXT_CONFIG_ID  / _VERSION
"""
import os
from typing import Dict, List, Optional, Tuple

import frappe
import requests

DEFAULT_BASE_URL = "https://api.kaapi.ai"
API_PREFIX = "/api/v1"


def _kaapi_api_key() -> str:
    """Env first, then the active Kaapi LLM Settings row (api_secret/api_key)."""
    env = os.environ.get("KAAPI_API_KEY", "").strip()
    if env:
        return env
    try:
        rows = frappe.get_list(
            "LLM Settings", filters={"is_active": 1, "provider": "Kaapi"}, limit=1
        )
        if rows:
            settings = frappe.get_doc("LLM Settings", rows[0].name)
            for field in ("api_secret", "api_key"):
                try:
                    value = settings.get_password(field)
                except Exception:
                    value = None
                if value:
                    return str(value).strip()
    except Exception:
        pass
    return ""


class KaapiAssessmentClient:
    TIMEOUT_SECONDS = 60

    def __init__(self):
        self.api_key = _kaapi_api_key()
        if not self.api_key:
            raise ValueError("Kaapi API key not configured (KAAPI_API_KEY or LLM Settings)")
        self.base_url = self._normalize_base(
            os.environ.get("KAAPI_BASE_URL", DEFAULT_BASE_URL)
        )
        self.callback_url = os.environ.get("KAAPI_CALLBACK_URL", "").strip()

    # ---- config lookup ----
    def _config_for(self, media_type: str) -> Tuple[Optional[str], int]:
        prefix = "KAAPI_TEXT_CONFIG" if media_type == "text" else "KAAPI_IMAGE_CONFIG"
        config_id = os.environ.get(f"{prefix}_ID", "").strip() or None
        try:
            version = int(os.environ.get(f"{prefix}_VERSION", "1"))
        except ValueError:
            version = 1
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

    # ---- public ----
    def build_row(self, submission_id: str, rendered_prompt: str,
                  media_type: str, image_url: Optional[str] = None) -> Dict:
        """One `input.data` row shaped to the config's input_schema."""
        row = {"submission_id": submission_id, "rendered_prompt": rendered_prompt}
        if media_type != "text":
            row["artwork_image"] = image_url
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
                f"(set KAAPI_{'TEXT' if media_type == 'text' else 'IMAGE'}_CONFIG_ID)"
            )
        if not self.callback_url:
            raise ValueError("KAAPI_CALLBACK_URL is not set — assessments are webhook-only")

        body = {
            "config": {"id": config_id, "version": version},
            "input": {"data": rows},
            "callback_url": self.callback_url,
            "request_metadata": request_metadata or {},
        }
        data = self._post("/assessments", body)
        assessment_id = data.get("assessment_id")
        if not assessment_id:
            raise Exception(f"Kaapi /assessments returned no assessment_id: {data}")
        return assessment_id
