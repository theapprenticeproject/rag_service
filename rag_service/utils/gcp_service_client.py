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


def is_gcs_url(url: str) -> bool:
    """Check if a URL is a GCS URL."""
    if not url:
        return False
    return (
        url.startswith("gs://")
        or url.startswith("https://storage.googleapis.com/")
        or url.startswith("http://storage.googleapis.com/")
    )


class GCPServiceClient:
    """Shared GCP client factory/helpers using credentials stored in Frappe."""

    def __init__(self):
        self.app_env = os.environ.get("APP_ENV", "production")
        settings = frappe.get_single("GCS Settings")
        raw_key = (settings.get("credentials_json") or "").strip()

        # If credentials are missing/invalid, we only allow initialization
        # if the later download_media call receives a non-GCS URL (in non-prod).
        # We store the raw key and only raise ValueError if actually needed.
        self.raw_key = raw_key
        self.settings = settings
        self.client = None
        self.key_data = {}

        if raw_key:
            try:
                self.key_data = json.loads(raw_key)
                self.project_id = settings.get("project_id") or self.key_data.get(
                    "project_id"
                )
                credentials = service_account.Credentials.from_service_account_info(
                    self.key_data
                )
                self.client = storage.Client(
                    project=self.project_id, credentials=credentials
                )
            except Exception:
                # We don't raise here yet; we'll raise in download_media if a GCS URL is used.
                pass

    def download_media(self, media_url: str) -> Dict:
        if not is_gcs_url(media_url):
            # Fallback for regular HTTP/S URLs - ONLY ALLOWED IN NON-PRODUCTION
            if self.app_env != "production" and media_url.startswith(
                ("http://", "https://")
            ):
                print(f"NON-PROD FALLBACK: Downloading media from HTTP/S: {media_url}")
                try:
                    from io import BytesIO

                    import requests

                    response = requests.get(media_url, timeout=30)
                    response.raise_for_status()
                    content = response.content

                    # Create a temporary file to match the expected return structure
                    suffix = Path(urlparse(media_url).path).suffix or ".png"
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=suffix
                    ) as temp_file:
                        temp_file.write(content)
                        temp_path = temp_file.name

                    mime_type = (
                        response.headers.get("Content-Type")
                        or mimetypes.guess_type(media_url)[0]
                        or "image/png"
                    )

                    return {
                        "bucket": "external-http",
                        "object_name": media_url,
                        "local_path": temp_path,
                        "mime_type": mime_type,
                        "content": content,
                        "filename": os.path.basename(urlparse(media_url).path)
                        or "downloaded_image",
                    }
                except Exception as e:
                    raise RuntimeError(f"HTTP/S download failed: {str(e)}")

        # If it IS a GCS URL (or if fallback failed/is disallowed), we must enforce valid credentials
        if not self.client:
            if not self.raw_key:
                raise ValueError(
                    f"GCS Settings.credentials_json is required for URL: {media_url}"
                )
            raise ValueError(
                f"GCS Settings.credentials_json must contain valid JSON for URL: {media_url}"
            )

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
