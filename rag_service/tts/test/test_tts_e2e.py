import json
import traceback
from contextlib import contextmanager

import frappe

from ..constants import (
    RUN_DOCTYPE,
    FEEDBACK_REQUEST_DOCTYPE,
    SETTINGS_DOCTYPE,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_FAILURES,
    FORMAT_OGG,
)
from ..lib import settings as settings_lib
from ..lib import languages as languages_lib
from ..lib import tokens as tokens_lib
from ..lib import contract as contract_lib
from ..lib.text import clean_text

TEST_RUN_PREFIX = "TTS-E2E-TEST"
TEST_SHARED_SECRET = "e2e-test-shared-secret-do-not-use-in-prod"
TEST_GCS_PREFIX = "tts-e2e-test"
TEST_CDN_BASE_URL = "https://cdn.example.test"
TEST_MODEL_REVISION = "e2e-test-rev-1"

SNAPSHOT_SECRET_FIELDS = (
    "runpod_api_key",
    "tts_pod_shared_secret",
    "tts_gcs_service_account_json_b64",
)

SNAPSHOT_SCHEDULING_FIELDS = (
    "scheduling_enabled",
    "schedule_mode",
    "schedule_interval_minutes",
    "schedule_cron_expressions",
    "capacity_retry_enabled",
    "capacity_retry_minutes",
)

STUDENTS = [
    {"name": "Durva", "student_id": "E2E-STU-DURVA", "language": "English",
     "text": "Great work Durva! Your artwork shows wonderful use of color and bold outlines. Keep practicing your shading technique and you will improve even more."},
    {"name": "Om", "student_id": "E2E-STU-OM", "language": "Hindi",
     "text": "ओम, तुमने बहुत अच्छा काम किया है! तुम्हारी ड्राइंग में रंगों का प्रयोग शानदार है। अभ्यास जारी रखो।"},
    {"name": "Manu", "student_id": "E2E-STU-MANU", "language": "Marathi",
     "text": "मनू, तुझं काम खूप छान आहे! रंगसंगती उत्तम आहे. असाच सराव सुरू ठेव आणि अजून सुधारणा कर."},
    {"name": "Mayank", "student_id": "E2E-STU-MAYANK", "language": "Punjabi",
     "text": "ਮਯੰਕ, ਤੁਹਾਡਾ ਕੰਮ ਬਹੁਤ ਵਧੀਆ ਹੈ! ਰੰਗਾਂ ਦੀ ਵਰਤੋਂ ਸ਼ਾਨਦਾਰ ਹੈ। ਅਭਿਆਸ ਜਾਰੀ ਰੱਖੋ।"},
    {"name": "Himani", "student_id": "E2E-STU-HIMANI", "language": "Kannada",
     "text": "ಹಿಮಾನಿ, ನಿಮ್ಮ ಕೆಲಸ ತುಂಬಾ ಚೆನ್ನಾಗಿದೆ! ಬಣ್ಣಗಳ ಬಳಕೆ ಅದ್ಭುತವಾಗಿದೆ. ಅಭ್ಯಾಸ ಮುಂದುವರಿಸಿ."},
]


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, label, detail=""):
        self.passed.append((label, detail))
        print(f"[PASS] {label} {('- ' + detail) if detail else ''}")

    def fail(self, label, detail=""):
        self.failed.append((label, detail))
        print(f"[FAIL] {label} {('- ' + detail) if detail else ''}")

    def summary(self):
        total = len(self.passed) + len(self.failed)
        print("\n" + "=" * 72)
        print(f"TTS E2E TEST SUMMARY: {len(self.passed)}/{total} passed")
        if self.failed:
            print("FAILURES:")
            for label, detail in self.failed:
                print(f"  - {label}: {detail}")
        print("=" * 72)
        return len(self.failed) == 0


@contextmanager
def _patched_outbound_queue(sent_messages):
    from ...core import feedback_service as feedback_service_module

    class _FakeQueueManager:
        def send_feedback_to_tap(self, message):
            sent_messages.append(message)

    real_init = feedback_service_module.FeedbackService.__init__

    def patched_init(self):
        real_init(self)
        self.queue_manager = _FakeQueueManager()

    feedback_service_module.FeedbackService.__init__ = patched_init
    try:
        yield
    finally:
        feedback_service_module.FeedbackService.__init__ = real_init


def _snapshot_tts_settings():
    doc = frappe.get_single(SETTINGS_DOCTYPE)
    snapshot = {}
    for field in settings_lib.SNAPSHOT_FIELDS:
        snapshot[field] = doc.get(field)
    for field in SNAPSHOT_SCHEDULING_FIELDS:
        if hasattr(doc, field):
            snapshot[field] = doc.get(field)
    for field in SNAPSHOT_SECRET_FIELDS:
        snapshot[field] = doc.get_password(field, raise_exception=False)
    return snapshot


def _restore_tts_settings(snapshot):
    doc = frappe.get_single(SETTINGS_DOCTYPE)
    for field, value in snapshot.items():
        doc.set(field, value)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    settings_lib.load_fresh()


def _ensure_test_tts_settings():
    doc = frappe.get_single(SETTINGS_DOCTYPE)
    doc.enabled = 1
    doc.allow_force_start = 1
    doc.min_items_to_deploy = 1
    doc.max_clips_per_run = 50
    doc.max_chars_per_text = 300
    doc.over_length_policy = "truncate"
    doc.supported_languages = (
        "english=English (Female)\n"
        "hindi=Hindi (Female)\n"
        "marathi=Marathi (Female)\n"
        "kannada=Kannada (Female)\n"
        "punjabi=Punjabi (Female)"
    )
    doc.fallback_hourly_rate_usd = 0.34
    doc.max_cost_per_run_usd = 50.00
    doc.max_cost_per_day_usd = 100.00
    doc.max_cost_per_month_usd = 1000.00
    doc.estimate_safety_factor = 1.3
    if hasattr(doc, "est_startup_seconds"):
        doc.est_startup_seconds = 5
    if hasattr(doc, "est_seconds_per_clip"):
        doc.est_seconds_per_clip = 1.0
    doc.pod_limit_max_seconds = 7200
    doc.startup_deadline_minutes = 15
    doc.heartbeat_timeout_minutes = 6
    doc.token_buffer_minutes = 30
    doc.termination_check_delay_seconds = 0
    if hasattr(doc, "max_consecutive_bad_runs"):
        doc.max_consecutive_bad_runs = 5
    if hasattr(doc, "bad_run_success_floor_percent"):
        doc.bad_run_success_floor_percent = 20
    if hasattr(doc, "alert_email"):
        doc.alert_email = "alerts@example.test"
    doc.runpod_api_base = "https://rest.runpod.io/v1"
    doc.image_name = "example/tts-pod@sha256:" + ("a" * 64)
    doc.gpu_type_ids = "NVIDIA RTX A5000"
    doc.gpu_count = 1
    doc.container_disk_gb = 40
    doc.http_timeout_seconds = 20
    doc.runpod_api_key = "e2e-test-runpod-key"
    doc.tts_pod_shared_secret = TEST_SHARED_SECRET
    doc.tts_gcs_service_account_json_b64 = "ZmFrZS1zZXJ2aWNlLWFjY291bnQtanNvbg=="
    doc.public_base_url = "https://e2e-test.example.com"
    doc.api_module_path = "rag_service.tts.api.pod"
    doc.gcs_bucket = "e2e-test-bucket"
    doc.gcs_prefix = TEST_GCS_PREFIX
    doc.cdn_base_url = TEST_CDN_BASE_URL
    doc.model_revision = TEST_MODEL_REVISION
    doc.verify_urls_on_write = 0
    if hasattr(doc, "scheduling_enabled"):
        doc.scheduling_enabled = 0
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    settings_lib.load_fresh()
    return doc


def _cleanup_previous_test_data():
    old_runs = frappe.get_all(RUN_DOCTYPE, filters={"triggered_by": "e2e-test"}, pluck="name")
    for run_name in old_runs:
        frappe.db.sql(f"DELETE FROM `tab{RUN_DOCTYPE}` WHERE name=%s", (run_name,))
    old_frs = frappe.get_all(
        FEEDBACK_REQUEST_DOCTYPE,
        filters={"student_id": ["like", "E2E-STU-%"]},
        pluck="name",
    )
    for fr_name in old_frs:
        frappe.delete_doc(FEEDBACK_REQUEST_DOCTYPE, fr_name, force=True, ignore_permissions=True)
    frappe.db.commit()


def _build_feedback_payload(student, language):
    cleaned_preview = clean_text(student["text"])
    return {
        "overall_feedback": f"Feedback for {student['name']}",
        "overall_feedback_translated": student["text"],
        "translation_language": language,
        "strengths": ["cost:0.01", "Feedback_LP:0.9", "Eval_LP:0.9"],
        "areas_for_improvement": ["Keep practicing."],
        "learning_objectives_feedback": ["Objective met."],
        "final_grade": 85,
        "submission_validity": "Valid",
        "encouragement": "Keep going!",
        "rubric_evaluations": [
            {"Skill": "Creativity", "grade_value": 4, "observation": "Strong color use."}
        ],
        "plagiarism_output": {
            "is_plagiarized": False,
            "is_ai_generated": False,
            "match_type": "original",
            "plagiarism_source": "none",
            "similarity_score": 0.0,
            "ai_detection_source": "none",
            "ai_confidence": 0.0,
            "similar_sources": [],
        },
        "_preview_cleaned_text": cleaned_preview,
    }


def _create_feedback_request(student, language):
    doc = frappe.new_doc(FEEDBACK_REQUEST_DOCTYPE)
    doc.update({
        "submission_id": f"E2E-SUB-{student['student_id']}",
        "student_id": student["student_id"],
        "assignment_id": "E2E-ASSIGN-001",
        "grade": "5",
        "level": "2",
        "language": language,
        "submission_type": "image",
        "submission_url": "",
        "submission_text": "",
        "submission_content": "",
        "plagiarism_score": 0.0,
        "is_plagiarized": 0,
        "plagiarism_source": "none",
        "match_type": "original",
        "is_ai_generated": 0,
        "ai_confidence": 0.0,
        "similar_sources": json.dumps([]),
        "ai_detection_source": "none",
        "status": "Processing",
        "created_at": frappe.utils.now_datetime(),
        "processing_attempts": 1,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _run_feedback_service_process_feedback(result, fr_name, student, language):
    import asyncio
    from ...core.feedback_service import FeedbackService

    feedback = _build_feedback_payload(student, language)
    feedback.pop("_preview_cleaned_text", None)

    fs = FeedbackService()
    asyncio.run(fs.process_feedback(fr_name, feedback, "e2e-test-model", "e2e-test-template"))

    row = frappe.get_doc(FEEDBACK_REQUEST_DOCTYPE, fr_name)

    if row.audio_status not in ("Queued", "Not Applicable"):
        result.fail(
            f"audio_status after process_feedback [{student['name']}/{language}]",
            f"expected Queued or Not Applicable, got {row.audio_status!r}",
        )
    else:
        result.ok(
            f"audio_status after process_feedback [{student['name']}/{language}]",
            f"audio_status={row.audio_status}",
        )

    normalized = languages_lib.normalize(language)
    if normalized is None:
        if row.audio_status != "Not Applicable":
            result.fail(
                f"unsupported language should short-circuit [{student['name']}/{language}]",
                f"got audio_status={row.audio_status}",
            )
        else:
            result.ok(f"unsupported language short-circuit [{student['name']}/{language}]")
        return row, normalized

    if row.audio_status != "Queued":
        result.fail(
            f"supported language should queue [{student['name']}/{language}]",
            f"got audio_status={row.audio_status}",
        )
        return row, normalized

    if not row.audio_text_fingerprint:
        result.fail(f"fingerprint set at queue time [{student['name']}/{language}]", "fingerprint empty")
    else:
        result.ok(f"fingerprint set at queue time [{student['name']}/{language}]")

    if row.sent_to_lms_at:
        result.fail(
            f"queued row must NOT be sent to LMS yet [{student['name']}/{language}]",
            "sent_to_lms_at was already set before the batch ran",
        )
    else:
        result.ok(f"queued row correctly held from LMS [{student['name']}/{language}]")

    return row, normalized


@contextmanager
def _patched_runpod_client():
    from ..lib import runpod as runpod_module

    real_client_cls = runpod_module.RunPodClient
    counter = {"n": 0}

    class _FakeRunPodClient:
        def __init__(self, base_url, api_key, timeout_seconds=20):
            self.base_url = base_url
            self.api_key = api_key
            self.timeout_seconds = timeout_seconds

        def create_pod(self, payload):
            counter["n"] += 1
            return {
                "id": f"e2e-fake-pod-{counter['n']}",
                "name": payload.get("name"),
                "costPerHr": 0.34,
            }

        def find_by_name(self, name):
            return None

        def get_pod(self, pod_id):
            return None

        def delete_pod(self, pod_id):
            return True

        def stop_pod(self, pod_id):
            return True

        def ping(self):
            return True

    runpod_module.RunPodClient = _FakeRunPodClient
    try:
        yield
    finally:
        runpod_module.RunPodClient = real_client_cls


def _run_deploy(result):
    from ..job import deploy as deploy_module

    settings_lib.load_fresh()
    with _patched_runpod_client():
        outcome = deploy_module.deploy_new(trigger_type="Manual", triggered_by="e2e-test", force=True)

    if not outcome.get("deployed"):
        result.fail("deploy_new() should deploy", f"got {outcome}")
        return None

    run_name = outcome["run"]
    run = frappe.get_doc(RUN_DOCTYPE, run_name)

    if run.status != "Starting":
        result.fail(
            "run status after deploy should be Starting (pod adopted in test mode)",
            f"got {run.status}",
        )
    else:
        result.ok("run status after deploy is Starting", f"run={run_name}")

    if not run.pod_id:
        result.fail("run should have a pod_id recorded", "pod_id empty")
    else:
        result.ok("run has pod_id recorded", f"pod_id={run.pod_id}")

    return run_name


def _issue_pod_token(run_name):
    settings = settings_lib.load()
    shared = settings.get_secret("tts_pod_shared_secret")
    ttl_minutes = int(settings.pod_limit_max_seconds / 60) + int(settings.token_buffer_minutes)
    return tokens_lib.issue(run_name, shared, ttl_minutes)


@contextmanager
def _as_pod_request(run_name, token):
    original_method = getattr(frappe.local, "request", None)

    class _FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class _FakeRequest:
        method = "POST"
        content_length = 10

    frappe.local.request = _FakeRequest()

    original_get_header = frappe.get_request_header

    def fake_get_request_header(key, default=None):
        mapping = {"X-Run-Id": run_name, "X-Run-Token": token}
        return mapping.get(key, default)

    frappe.get_request_header = fake_get_request_header

    original_request_ip = getattr(frappe.local, "request_ip", None)
    frappe.local.request_ip = "127.0.0.1"

    try:
        yield
    finally:
        frappe.local.request = original_method
        frappe.get_request_header = original_get_header
        frappe.local.request_ip = original_request_ip


def _call_manifest(run_name, token):
    from ..api import pod as pod_api

    with _as_pod_request(run_name, token):
        return pod_api.manifest()


def _call_beat(run_name, token, phase, remaining):
    from ..api import pod as pod_api

    frappe.local.form_dict = frappe._dict({"phase": phase, "remaining": remaining})
    with _as_pod_request(run_name, token):
        return pod_api.beat()


def _call_progress(run_name, token, records):
    from ..api import pod as pod_api

    frappe.local.form_dict = frappe._dict({"records": records})
    with _as_pod_request(run_name, token):
        return pod_api.progress()


def _call_complete(run_name, token, reason, stats):
    from ..api import pod as pod_api

    frappe.local.form_dict = frappe._dict({"reason": reason, "stats": stats})
    with _as_pod_request(run_name, token):
        return pod_api.complete()


def _simulate_pod_lifecycle(result, run_name, expected_rows_by_id):
    settings = settings_lib.load()
    token = _issue_pod_token(run_name)

    manifest_response = _call_manifest(run_name, token)
    items = manifest_response.get("items", [])

    if len(items) != len(expected_rows_by_id):
        result.fail(
            "manifest item count matches expected eligible rows",
            f"expected {len(expected_rows_by_id)}, got {len(items)}",
        )
    else:
        result.ok("manifest item count matches expected eligible rows", f"count={len(items)}")

    manifest_ids = {item["id"] for item in items}
    expected_ids = set(expected_rows_by_id.keys())
    if manifest_ids != expected_ids:
        result.fail(
            "manifest ids match expected ids",
            f"missing={expected_ids - manifest_ids} extra={manifest_ids - expected_ids}",
        )
    else:
        result.ok("manifest ids match expected ids")

    second_manifest = _call_manifest(run_name, token)
    second_items = second_manifest.get("items", [])
    if len(second_items) != len(items):
        result.fail(
            "repeat manifest call returns the same item count (idempotent)",
            f"first={len(items)} second={len(second_items)}",
        )
    else:
        result.ok("repeat manifest call returns the same item count (idempotent)")

    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if run.status != "Running":
        result.fail("run flips to Running after manifest while Starting", f"got {run.status}")
    else:
        result.ok("run flips to Running after manifest while Starting")

    if run.manifest_count != len(items):
        result.fail(
            "manifest_count unaffected by repeat manifest call",
            f"expected {len(items)}, got {run.manifest_count}",
        )
    else:
        result.ok("manifest_count unaffected by repeat manifest call")

    beat_response = _call_beat(run_name, token, phase="generating", remaining=len(items))
    if beat_response.get("stop"):
        result.fail("beat() should not request stop mid-run", f"got {beat_response}")
    else:
        result.ok("beat() reports stop=False mid-run")

    records = []
    for item in items:
        fr_name = item["id"]
        text = item["text"]
        language = item["language"]
        digest_hash = contract_lib.expected_hash(text, language, settings.model_revision, FORMAT_OGG)
        url = contract_lib.expected_url(
            text, language, settings.model_revision, settings.gcs_prefix, settings.cdn_base_url, FORMAT_OGG
        )
        records.append({
            "id": fr_name,
            "status": "ok",
            "content_hash": digest_hash,
            "url": url,
        })

    progress_response = _call_progress(run_name, token, records)

    if progress_response.get("written") != len(records):
        result.fail(
            "progress() wrote every submitted record",
            f"expected written={len(records)}, got {progress_response}",
        )
    else:
        result.ok("progress() wrote every submitted record", f"written={progress_response.get('written')}")

    for fr_name in expected_rows_by_id:
        row = frappe.get_doc(FEEDBACK_REQUEST_DOCTYPE, fr_name)
        if row.audio_status != "Completed":
            result.fail(f"row Completed after progress [{fr_name}]", f"got {row.audio_status}")
            continue
        if not row.audio_feedback_url:
            result.fail(f"row has audio_feedback_url after progress [{fr_name}]", "url empty")
        else:
            result.ok(f"row has audio_feedback_url after progress [{fr_name}]")
        if not row.audio_content_hash:
            result.fail(f"row has audio_content_hash after progress [{fr_name}]", "hash empty")
        else:
            result.ok(f"row has audio_content_hash after progress [{fr_name}]")
        if not row.sent_to_lms_at:
            result.fail(
                f"row released to LMS immediately after its own progress write [{fr_name}]",
                "sent_to_lms_at still empty",
            )
        else:
            result.ok(f"row released to LMS immediately after its own progress write [{fr_name}]")

    complete_response = _call_complete(
        run_name, token, reason="completed",
        stats={
            "uploaded": len(records), "generated": len(records), "decoded": len(records),
            "truncated": 0, "retried": 0, "tokens": 1234,
            "audio_seconds": float(len(records) * 4), "startup_seconds": 5.0,
            "run_seconds": 12.0,
        },
    )

    if not complete_response.get("accepted"):
        result.fail("complete() accepted the final report", f"got {complete_response}")
    else:
        result.ok("complete() accepted the final report")

    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if run.status not in (STATUS_COMPLETED, STATUS_COMPLETED_WITH_FAILURES):
        result.fail("run reaches a Completed-family status", f"got {run.status}")
    else:
        result.ok("run reaches a Completed-family status", f"status={run.status}")

    if run.not_processed not in (0, None):
        result.fail("not_processed is 0 for a fully-completed run", f"got {run.not_processed}")
    else:
        result.ok("not_processed is 0 for a fully-completed run")

    return run


def _verify_lms_messages(result, sent_messages, students_by_submission):
    sent_by_submission = {msg["submission_id"]: msg for msg in sent_messages}

    for student in students_by_submission:
        submission_id = f"E2E-SUB-{student['student_id']}"
        language = student["language"]

        if submission_id not in sent_by_submission:
            result.fail(
                f"LMS message sent for [{student['name']}/{language}]",
                "no message with this submission_id was sent",
            )
            continue

        msg = sent_by_submission[submission_id]
        feedback = msg.get("feedback", {})
        audio_url = feedback.get("audio_feedback_url", "")
        normalized = languages_lib.normalize(language)

        if normalized is None:
            if audio_url:
                result.fail(
                    f"unsupported-language message has no audio url [{student['name']}/{language}]",
                    f"got url={audio_url}",
                )
            else:
                result.ok(f"unsupported-language message correctly has empty audio url [{student['name']}/{language}]")
        else:
            if not audio_url:
                result.fail(
                    f"supported-language message carries audio url [{student['name']}/{language}]",
                    "audio_feedback_url empty in outbound message",
                )
            elif not audio_url.startswith(TEST_CDN_BASE_URL):
                result.fail(
                    f"audio url uses configured CDN base [{student['name']}/{language}]",
                    f"got {audio_url}",
                )
            else:
                result.ok(f"supported-language message carries correct CDN audio url [{student['name']}/{language}]")

        if msg.get("student_id") != student["student_id"]:
            result.fail(
                f"outbound message student_id matches [{student['name']}]",
                f"expected {student['student_id']}, got {msg.get('student_id')}",
            )
        else:
            result.ok(f"outbound message student_id matches [{student['name']}]")


def _test_idempotent_second_send(result, fr_name, sent_messages_before_count, sent_messages):
    import asyncio
    from ...core.feedback_service import FeedbackService

    fs = FeedbackService()
    asyncio.run(fs.release_to_lms_if_ready(fr_name))

    if len(sent_messages) != sent_messages_before_count:
        result.fail(
            f"release_to_lms_if_ready is idempotent [{fr_name}]",
            f"message count changed from {sent_messages_before_count} to {len(sent_messages)}",
        )
    else:
        result.ok(f"release_to_lms_if_ready is idempotent [{fr_name}]")


def _test_reaper_releases_stuck_run(result):
    from ..job import deploy as deploy_module
    from ..job.reaper import reap_one

    student = {
        "name": "ReaperTest", "student_id": "E2E-STU-REAPER", "language": "English",
        "text": "Short reaper test feedback text for timeout handling verification.",
    }
    fr_name = _create_feedback_request(student, student["language"])
    _run_feedback_service_process_feedback(result, fr_name, student, student["language"])

    settings_lib.load_fresh()
    with _patched_runpod_client():
        outcome = deploy_module.deploy_new(trigger_type="Manual", triggered_by="e2e-test", force=True)
    if not outcome.get("deployed"):
        result.fail("reaper test: deploy_new() should deploy", f"got {outcome}")
        return
    run_name = outcome["run"]

    frappe.db.set_value(
        RUN_DOCTYPE, run_name,
        {"last_beat_at": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-30)},
        update_modified=False,
    )
    frappe.db.set_value(RUN_DOCTYPE, run_name, "status", "Running", update_modified=False)
    frappe.db.commit()

    with _patched_runpod_client():
        acted = reap_one(run_name)
    if not acted:
        result.fail("reaper reaps a run with stale heartbeat", "reap_one returned False")
    else:
        result.ok("reaper reaps a run with stale heartbeat")

    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    if run.status != "Lost":
        result.fail("reaped run status is Lost", f"got {run.status}")
    else:
        result.ok("reaped run status is Lost")

    if getattr(run, "not_processed", None) != 1:
        result.fail("reaped run counts the stuck row as not_processed", f"got {getattr(run, 'not_processed', None)}")
    else:
        result.ok("reaped run counts the stuck row as not_processed")

    row = frappe.get_doc(FEEDBACK_REQUEST_DOCTYPE, fr_name)
    if row.audio_status != "Failed":
        result.fail("stuck Feedback Request released as Failed by reaper", f"got {row.audio_status}")
    else:
        result.ok("stuck Feedback Request released as Failed by reaper")

    if not row.sent_to_lms_at:
        result.fail("stuck Feedback Request still gets sent to LMS after reaper", "sent_to_lms_at empty")
    else:
        result.ok("stuck Feedback Request still gets sent to LMS after reaper (fail-open)")


def run_all():
    result = TestResult()
    sent_messages = []
    snapshot = _snapshot_tts_settings()

    try:
        print("=" * 72)
        print("TTS END-TO-END TEST — 5 students x 5 languages")
        print("=" * 72)

        print("\n--- Setup ---")
        _ensure_test_tts_settings()
        result.ok("TTS Settings configured for test")
        _cleanup_previous_test_data()
        result.ok("previous test data cleaned up")

        with _patched_outbound_queue(sent_messages):
            print("\n--- Phase 1: create Feedback Request rows + process_feedback (as rag_service real pipeline does) ---")
            fr_names_by_id = {}
            for student in STUDENTS:
                fr_name = _create_feedback_request(student, student["language"])
                row, normalized = _run_feedback_service_process_feedback(result, fr_name, student, student["language"])
                if normalized is not None and row.audio_status == "Queued":
                    fr_names_by_id[fr_name] = student

            print(f"\n{len(fr_names_by_id)} rows queued for audio out of {len(STUDENTS)} students.")

            print("\n--- Phase 2: deploy_new() — plan + create TTS Run + 'launch pod' ---")
            run_name = _run_deploy(result)

            if run_name:
                print("\n--- Phase 3: simulate the pod's full lifecycle calling back into rag_service ---")
                _simulate_pod_lifecycle(result, run_name, fr_names_by_id)

            print("\n--- Phase 4: verify every student's message was actually released to the LMS queue ---")
            _verify_lms_messages(result, sent_messages, STUDENTS)

            unsupported_students = [
                s for s in STUDENTS if languages_lib.normalize(s["language"]) is None
            ]
            if not unsupported_students:
                result.ok("all five test languages are configured as supported (expected for this test)")
            print(f"\nTotal LMS messages sent: {len(sent_messages)}")

            print("\n--- Phase 5: idempotency — release_to_lms_if_ready must not double-send ---")
            if fr_names_by_id:
                any_fr_name = next(iter(fr_names_by_id))
                _test_idempotent_second_send(result, any_fr_name, len(sent_messages), sent_messages)

            print("\n--- Phase 6: reaper safety net — stuck row must be released, never stuck forever ---")
            _test_reaper_releases_stuck_run(result)

    except Exception as exc:
        result.fail("unexpected exception during test run", f"{exc}\n{traceback.format_exc()}")

    finally:
        frappe.db.commit()
        _restore_tts_settings(snapshot)

    success = result.summary()
    return {"success": success, "passed": len(result.passed), "failed": len(result.failed)}


if __name__ == "__main__":
    run_all()