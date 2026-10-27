"""Submission audit regression checks without a running Frappe site or Kaapi calls."""
import importlib.util
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests


APP_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_MODULE = "rag_service.rag_service.doctype.kaapi_settings.kaapi_settings"


class TestKaapiSubmissionLogging(unittest.TestCase):
    def setUp(self):
        self.frappe = Mock()
        self.frappe.whitelist.return_value = lambda function: function
        self.frappe.utils.now_datetime.return_value = "2026-09-29 12:00:00"
        self.document = Mock()
        self.document.name = "LOG-001"
        self.frappe.get_doc.return_value = self.document

        settings = Mock(base_url="https://kaapi.example", allow_insecure_webhook=False)
        settings.get_secret.return_value = "api-secret"
        settings.get_callback_url.return_value = (
            "https://rag.example/callback?site=rag&empty="
        )
        settings.get.side_effect = {
            "image_config_id": "image-config", "image_config_version": 2,
            "text_config_id": "text-config", "text_config_version": 3,
            "video_config_id": "video-config", "video_config_version": 4,
        }.get
        settings_module = ModuleType(SETTINGS_MODULE)
        settings_module.DEFAULT_BASE_URL = "https://kaapi.example"
        settings_module.get_kaapi_settings = lambda: settings
        settings_module.assessments_enabled = lambda: True
        self.settings = settings
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {
            "frappe": self.frappe,
            SETTINGS_MODULE: settings_module,
        }).start()
        self.module = self._load("core.kaapi_assessment")
        self.client = self.module.KaapiAssessmentClient()
        self.response = Mock(ok=True, status_code=200)
        self.response.json.return_value = {
            "success": True, "data": {"assessment_id": "assessment-123"},
        }
        self.post = patch.object(self.module.requests, "post", return_value=self.response).start()
        self.rows = [{"submission_id": "FR-1", "rendered_prompt": "Grade this 👍"}]

    @staticmethod
    def _load(relative_name):
        spec = importlib.util.spec_from_file_location(
            f"rag_service.{relative_name}", APP_ROOT / (relative_name.replace(".", "/") + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _saved_fields(self):
        return self.frappe.get_doc.call_args.args[0]

    def _updated_fields(self):
        return self.frappe.db.set_value.call_args.args[2]

    def test_full_batch_is_committed_before_sending_without_mutating_request(self):
        rows = [
            {"submission_id": "FR-1", "rendered_prompt": "बहुत अच्छा 👍\n" * 10000,
             "artwork_image": "gs://artwork/first.png"},
            {"submission_id": "FR-2", "rendered_prompt": "Last row {rubric}",
             "artwork_image": "gs://artwork/last.png"},
        ]
        metadata = {"kind": "image", "nested": {"labels": ["first", "last"]}}

        def respond(*args, **kwargs):
            self.document.insert.assert_called_once_with(ignore_permissions=True)
            self.frappe.db.commit.assert_called_once()
            self.assertEqual(self._saved_fields()["processing_status"], "Submitting")
            return self.response

        self.post.side_effect = respond
        self.assertEqual(self.client.submit(rows, "image", metadata), "assessment-123")
        saved = self._saved_fields()
        payload = json.loads(saved["raw_body"])
        sent = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["input"]["data"], rows)
        self.assertEqual(payload["config"], {"id": "image-config", "version": 2})
        self.assertEqual(payload["request_metadata"], metadata)
        self.assertEqual(set(payload), set(sent))
        self.assertEqual(
            {k: v for k, v in payload.items() if k != "callback_url"},
            {k: v for k, v in sent.items() if k != "callback_url"},
        )
        self.assertEqual(parse_qs(urlsplit(payload["callback_url"]).query, keep_blank_values=True),
                         {"site": ["rag"], "empty": [""]})
        self.assertEqual(sent["callback_url"], self.client.callback_url)
        self.assertNotIn("webhook-secret", saved["raw_body"])
        self.assertNotIn("api-secret", saved["raw_body"])
        self.assertEqual(saved["direction"], "Outgoing")
        self.assertEqual(saved["request_url"], self.post.call_args.args[0])
        self.assertEqual(self._updated_fields()["processing_status"], "Submitted")
        self.assertEqual(self._updated_fields()["assessment_id"], "assessment-123")

    def test_text_batch_and_default_metadata_are_saved(self):
        self.client.submit(self.rows, "text")
        payload = json.loads(self._saved_fields()["raw_body"])
        self.assertEqual(payload["input"]["data"], self.rows)
        self.assertEqual(payload["config"], {"id": "text-config", "version": 3})
        self.assertEqual(payload["request_metadata"], {})

    def test_video_batch_uses_video_config_and_normalizes_urls(self):
        row = self.client.build_row(
            "FR-1", "Grade this video", "video",
            "https://storage.googleapis.com/assignment_submission_tap/clip.mp4?X-Goog-Signature=s",
        )
        self.assertEqual(row, {"submission_id": "FR-1", "rendered_prompt": "Grade this video",
                               "artwork_video": "gs://assignment_submission_tap/clip.mp4"})
        self.client.submit([row], "video")
        payload = json.loads(self._saved_fields()["raw_body"])
        self.assertEqual(payload["config"], {"id": "video-config", "version": 4})
        self.assertEqual(payload["input"]["data"], [row])
        with self.assertRaisesRegex(ValueError, "Kaapi media URL"):
            self.client.submit(self.rows, "video")  # Missing artwork_video.

    def test_missing_video_config_names_the_video_field(self):
        self.client.settings.get.side_effect = {}.get
        with self.assertRaisesRegex(ValueError, "Video Config ID"):
            self.client.submit(self.rows, "video")
        self.post.assert_not_called()

    def test_image_batch_normalizes_gcs_urls_without_mutating_rows(self):
        cases = [
            ("https://storage.googleapis.com/assignment_submission_tap/submissions/image.jpg",
             "gs://assignment_submission_tap/submissions/image.jpg"),
            ("https://storage.googleapis.com/assignment_submission_tap/image.jpg"
             "?X-Goog-Signature=secret&X-Goog-Expires=3600#preview",
             "gs://assignment_submission_tap/image.jpg"),
            ("https://assignment_submission_tap.storage.googleapis.com/image.png",
             "gs://assignment_submission_tap/image.png"),
            ("https://storage.cloud.google.com/assignment_submission_tap/image.png?authuser=1",
             "gs://assignment_submission_tap/image.png"),
            ("https://storage.googleapis.com/another-bucket/folder/image%20%231%25.png",
             "gs://another-bucket/folder/image%20%231%25.png"),
            ("gs://assignment_submission_tap/submissions/image.jpg",
             "gs://assignment_submission_tap/submissions/image.jpg"),
            ("  gs://assignment_submission_tap/submissions/image.jpg  ",
             "gs://assignment_submission_tap/submissions/image.jpg"),
        ]
        rows = [
            {"submission_id": f"FR-{index}", "rendered_prompt": "Grade this image",
             "artwork_image": original}
            for index, (original, _) in enumerate(cases)
        ]
        original_rows = [dict(row) for row in rows]

        self.client.submit(rows, "image")

        sent_rows = self.post.call_args.kwargs["json"]["input"]["data"]
        logged_rows = json.loads(self._saved_fields()["raw_body"])["input"]["data"]
        self.assertEqual(logged_rows, sent_rows)
        self.assertEqual(rows, original_rows)
        self.assertEqual(len(sent_rows), len(rows))
        for original_row, sent_row, (_, expected_url) in zip(rows, sent_rows, cases):
            with self.subTest(url=original_row["artwork_image"]):
                self.assertEqual(sent_row, {**original_row, "artwork_image": expected_url})

    def test_invalid_image_urls_are_rejected_before_logging_or_sending(self):
        invalid_urls = [
            None, "", "   ", 123,
            "https://images.example/image.jpg?token=public-link",
            "https://storage.googleapis.com.example/image.jpg",
            "ftp://storage.googleapis.com/bucket/image.jpg",
            "https://storage.googleapis.com/assignment_submission_tap",
            "https://assignment_submission_tap.storage.googleapis.com/",
            "gs://", "gs:///image.jpg", "gs://bucket", "gs://bucket/",
            "gs://bucket/image.jpg?signature=secret",
            "gs://bucket/image.jpg#preview",
            "gs://user@bucket/image.jpg", "gs://bucket:443/image.jpg",
        ]
        for image_url in invalid_urls:
            with self.subTest(url=image_url):
                with self.assertRaisesRegex(ValueError, "Kaapi media URL"):
                    self.client.build_row("FR-1", "Grade", "image", image_url)
                # A bad row must prevent even the valid part of a directly submitted batch
                # from being sent, preserving batch order and webhook correlation.
                rows = [
                    self.client.build_row("FR-1", "Grade", "image", "gs://bucket/valid.jpg"),
                    {"submission_id": "FR-2", "rendered_prompt": "Grade",
                     "artwork_image": image_url},
                ]
                with self.assertRaisesRegex(ValueError, "Kaapi media URL"):
                    self.client.submit(rows, "image")
        with self.assertRaisesRegex(ValueError, "Kaapi media URL"):
            self.client.submit(self.rows, "image")  # Missing artwork_image.
        self.post.assert_not_called()
        self.frappe.get_doc.assert_not_called()

    def test_preparing_images_converts_urls_and_isolates_invalid_rows(self):
        import asyncio

        feedback_module = ModuleType("rag_service.core.feedback_service")
        feedback_module.FeedbackService = Mock()
        context_module = ModuleType("rag_service.core.assignment_context_manager")
        context_module.AssignmentContextManager = Mock()
        context_module.AssignmentContextManager.return_value.get_assignment_context = AsyncMock(
            return_value={"assignment": {"name": "Drawing"}}
        )
        image_module = ModuleType("rag_service.feedback_utils.image_evaluation_both")
        image_module.ImageEvaluationGenerator = Mock()
        image_module.ImageEvaluationGenerator.return_value.build_eval_request.side_effect = (
            lambda context, submission, **kwargs: {
                "combined_prompt": "Grade", "image_url": submission["submission_url"],
                "expected_format": {}, "model_used": "Kaapi Assessment", "template_used": "Image",
            }
        )
        text_module = ModuleType("rag_service.feedback_utils.text_evaluation")
        text_module.TextEvaluationGenerator = Mock()
        video_module = ModuleType("rag_service.feedback_utils.video_evaluation")
        video_module.VideoEvaluationGenerator = Mock()
        with patch.dict(sys.modules, {
            video_module.__name__: video_module,
            feedback_module.__name__: feedback_module,
            context_module.__name__: context_module,
            image_module.__name__: image_module,
            text_module.__name__: text_module,
            "rag_service.core.kaapi_assessment": self.module,
        }):
            batch = self._load("core.batch_runner")
            with patch.object(batch, "_deliver_flagged", new=AsyncMock(return_value=False)), \
                 patch.object(batch, "_message_from_request") as message, \
                 patch.object(batch, "_mark_failed") as mark_failed:
                self.document.submission_type = "image"
                message.side_effect = [
                    {"submission_type": "image", "submission_url": "https://example.com/bad.jpg"},
                    {"submission_type": "image", "submission_url":
                     "https://storage.googleapis.com/assignment_submission_tap/valid.jpg"},
                ]
                buckets = asyncio.run(batch._prepare_page(["FR-bad", "FR-good"]))
                self.assertEqual(buckets["failed"], 1)
                mark_failed.assert_called_once()
                self.assertEqual(mark_failed.call_args.args[0], "FR-bad")
                self.assertEqual(len(buckets["image"]), 1)
                fr_name, row, _ = buckets["image"][0]
                self.assertEqual(fr_name, "FR-good")
                self.assertEqual(row["artwork_image"],
                                 "gs://assignment_submission_tap/valid.jpg")
        self.post.assert_not_called()

    def test_timeout_retains_payload_and_records_error(self):
        self.post.side_effect = requests.Timeout("request timed out")
        with self.assertRaises(requests.Timeout):
            self.client.submit(self.rows, "text")
        self.assertEqual(json.loads(self._saved_fields()["raw_body"])["input"]["data"], self.rows)
        self.assertEqual(self._updated_fields()["processing_status"], "Error")
        self.assertEqual(self._updated_fields()["error"], "request timed out")
        self.assertEqual(self.frappe.db.commit.call_count, 2)

    def test_http_error_is_logged_and_propagated(self):
        self.response.ok = False
        self.response.status_code = 503
        self.response.text = "Unavailable"
        with self.assertRaisesRegex(Exception, "503"):
            self.client.submit(self.rows, "text")
        self.assertEqual(self._updated_fields()["processing_status"], "Error")
        self.assertIn("Unavailable", self._updated_fields()["error"])

    def test_missing_assessment_id_is_logged_as_error(self):
        self.response.json.return_value = {"success": True, "data": {}}
        with self.assertRaisesRegex(Exception, "no assessment_id"):
            self.client.submit(self.rows, "text")
        self.assertEqual(self._updated_fields()["processing_status"], "Error")

    def test_failed_initial_save_prevents_an_unlogged_http_request(self):
        self.document.insert.side_effect = RuntimeError("database unavailable")
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.client.submit(self.rows, "text")
        self.post.assert_not_called()

    def test_failed_log_update_does_not_discard_accepted_assessment_id(self):
        self.frappe.db.set_value.side_effect = RuntimeError("database unavailable")
        self.assertEqual(self.client.submit(self.rows, "text"), "assessment-123")
        self.frappe.db.rollback.assert_called_once()
        self.frappe.log_error.assert_called_once()

    def test_incoming_log_preserves_raw_body_and_redacts_credentials(self):
        webhook = self._load("api.kaapi_webhook")
        raw = '{"assessment_id": "assessment-123", "status": "COMPLETED", "extra": [1, 2]}'
        self.frappe.request.get_data.return_value = raw
        self.frappe.request.args = {"site": "rag"}
        self.frappe.request.headers = {
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
            "X-Webhook-Signature": "signed-secret",
            "X-Webhook-Timestamp": "1792082223157",
        }
        self.frappe.request.content_type = "application/json"
        self.assertEqual(webhook._log_request(), "LOG-001")
        saved = self._saved_fields()
        self.assertEqual(saved["direction"], "Incoming")
        self.assertEqual(saved["raw_body"], raw)
        self.assertEqual(saved["assessment_id"], "assessment-123")
        self.assertEqual(json.loads(saved["headers"])["Authorization"], "***")
        self.assertEqual(json.loads(saved["headers"])["X-Webhook-Signature"], "***")
        self.assertEqual(json.loads(saved["headers"])["X-Webhook-Timestamp"], "1792082223157")

    def test_kaapi_hmac_signature_uses_timestamp_dot_raw_body(self):
        webhook = self._load("api.kaapi_webhook")
        raw = '{"assessment_id":"assessment-123","feedback":"बहुत अच्छा 👍"}'.encode()
        timestamp = "1792082223157"
        signature = hmac.new(
            b"api-secret", timestamp.encode("ascii") + b"." + raw, hashlib.sha256
        ).hexdigest()
        headers = {
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": timestamp,
        }
        self.frappe.get_request_header.side_effect = headers.get

        with patch.object(webhook.time, "time_ns", return_value=int(timestamp) * 1_000_000):
            self.assertTrue(webhook._verify_signature(raw))
            self.assertFalse(webhook._verify_signature(raw + b" "))

    def test_kaapi_hmac_rejects_stale_or_missing_headers(self):
        webhook = self._load("api.kaapi_webhook")
        raw = b"{}"
        timestamp = "1792082223157"
        signature = hmac.new(
            b"api-secret", timestamp.encode("ascii") + b"." + raw, hashlib.sha256
        ).hexdigest()
        headers = {
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": timestamp,
        }
        self.frappe.get_request_header.side_effect = headers.get
        stale_now = (int(timestamp) + webhook._MAX_SIGNATURE_AGE_MS + 1) * 1_000_000
        with patch.object(webhook.time, "time_ns", return_value=stale_now):
            self.assertFalse(webhook._verify_signature(raw))

        self.frappe.get_request_header.side_effect = {}.get
        self.assertFalse(webhook._verify_signature(raw))

    def test_both_log_directions_queue_a_gcs_upload_after_commit(self):
        self.client.submit(self.rows, "text")
        webhook = self._load("api.kaapi_webhook")
        self.frappe.request.get_data.return_value = "{}"
        self.frappe.request.args = {}
        self.frappe.request.headers = {}
        webhook._log_request()
        jobs = [c for c in self.frappe.enqueue.call_args_list
                if c.args[0] == "rag_service.core.kaapi_log_archive.upload_raw_body"]
        self.assertEqual([c.kwargs["log_name"] for c in jobs], ["LOG-001", "LOG-001"])

    def test_failed_enqueue_does_not_block_submission(self):
        self.frappe.enqueue.side_effect = RuntimeError("redis unavailable")
        self.assertEqual(self.client.submit(self.rows, "text"), "assessment-123")
        self.post.assert_called_once()

    def test_upload_writes_raw_body_as_text_and_records_url(self):
        from datetime import datetime

        archive = self._load("core.kaapi_log_archive")
        self.frappe.utils.get_datetime.side_effect = lambda value: value
        blob = Mock()
        gcp = Mock()
        gcp.GCPServiceClient.return_value.client.bucket.return_value.blob.return_value = blob
        cases = [("Outgoing", "kaapi_logs_request"), ("Incoming", "kaapi_logs_response")]
        with patch.dict(sys.modules, {"rag_service.utils.gcp_service_client": gcp}):
            for direction, prefix in cases:
                with self.subTest(direction=direction):
                    self.frappe.db.get_value.return_value = frappe_dict(
                        direction=direction, received_at=datetime(2026, 9, 29, 12, 0, 5),
                        raw_body='{"text": "बहुत अच्छा 👍"}')
                    url = archive.upload_raw_body("LOG-001")
                    expected = f"kaapi_logs/{prefix}_20260929_120005_LOG-001.txt"
                    gcp.GCPServiceClient.return_value.client.bucket.assert_called_with(
                        "assignment_submission_tap")
                    gcp.GCPServiceClient.return_value.client.bucket.return_value.blob \
                        .assert_called_with(expected)
                    self.assertEqual(blob.upload_from_string.call_args.args[0],
                                     '{"text": "बहुत अच्छा 👍"}'.encode("utf-8"))
                    self.assertEqual(url, f"gs://assignment_submission_tap/{expected}")
                    self.frappe.db.set_value.assert_called_with(
                        "Kaapi Webhook Log", "LOG-001", "raw_body_gcs_url", url)


def frappe_dict(**fields):
    return type("FrappeDict", (dict,), {"__getattr__": dict.__getitem__})(fields)


if __name__ == "__main__":
    unittest.main()
