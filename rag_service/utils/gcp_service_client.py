import json
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

import frappe
from google.cloud import storage
from google.oauth2 import service_account


class GCPServiceClient:
    """Shared GCP client factory/helpers using credentials stored in Frappe."""

    def __init__(self):
        self.is_stub = os.environ.get("STUB_MODE") == "1"
        if self.is_stub:
            self.client = None
            self.key_data = {}
            return

        settings = frappe.get_single("GCS Settings")
        raw_key = (settings.get("credentials_json") or "").strip()
        if not raw_key:
            raise ValueError("GCS Settings.credentials_json is required")

        try:
            self.key_data = json.loads(raw_key)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GCS Settings.credentials_json must contain valid JSON"
            ) from exc

        self.project_id = settings.get("project_id") or self.key_data.get("project_id")
        credentials = service_account.Credentials.from_service_account_info(
            self.key_data
        )
        self.client = storage.Client(project=self.project_id, credentials=credentials)

    def download_media(self, media_url: str) -> Dict:
        if self.is_stub:
            print(f"STUB_MODE: Simulating media download for {media_url}")
            # Try to actually download if it's an http url, else return mock
            content = b""
            mime_type = "image/png"
            if media_url.startswith("http"):
                try:
                    import requests

                    resp = requests.get(media_url, timeout=5)
                    content = resp.content
                    mime_type = resp.headers.get("content-type", "image/png")
                except:
                    pass

            return {
                "bucket": "stub-bucket",
                "object_name": "stub-object",
                "local_path": None,
                "mime_type": mime_type,
                "content": content,
                "filename": "stub-file",
            }

        bucket_name, object_name = self._parse_gcs_url(media_url)
        blob = self.client.bucket(bucket_name).blob(object_name)

        suffix = Path(object_name).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name

        blob.download_to_filename(temp_path)
        mime_type = (
            blob.content_type
            or mimetypes.guess_type(object_name)[0]
            or "application/octet-stream"
        )
        with open(temp_path, "rb") as media_file:
            content = media_file.read()

        return {
            "bucket": bucket_name,
            "object_name": object_name,
            "local_path": temp_path,
            "mime_type": mime_type,
            "content": content,
            "filename": os.path.basename(object_name),
        }

    def cleanup(self, media_asset: Optional[Dict]) -> None:
        if not media_asset:
            return

        local_path = media_asset.get("local_path")
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

    def _parse_gcs_url(self, media_url: str) -> Tuple[str, str]:
        parsed = urlparse(media_url)

        if parsed.scheme == "gs":
            bucket = parsed.netloc
            object_name = parsed.path.lstrip("/")
            return bucket, unquote(object_name)

        host = parsed.netloc.lower()
        path = parsed.path.lstrip("/")

        if host == "storage.googleapis.com":
            bucket, object_name = path.split("/", 1)
            return bucket, unquote(object_name)

        if host.endswith(".storage.googleapis.com"):
            bucket = host[: -len(".storage.googleapis.com")]
            return bucket, unquote(path)

        if host == "storage.cloud.google.com":
            bucket, object_name = path.split("/", 1)
            return bucket, unquote(object_name)

        raise ValueError(f"Unsupported GCS media URL format: {media_url}")
