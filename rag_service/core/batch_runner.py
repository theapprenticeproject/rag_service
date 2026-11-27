# rag_service/rag_service/core/batch_runner.py
"""
Nightly batch grader.

Flow: image/text/video/audio submissions are NOT graded in real time —
`FeedbackHandler.handle_submission` saves them to the Feedback Request doctype with
status="Pending" (see `feedback_handler.batch_mode_enabled`). tapapp rows are submitted
to Kaapi immediately instead (see feedback_handler's fast-path), so this job's normal
fetch excludes them — they're not picked up by the scheduled run, only explicitly named.

This job grades all Pending image/text/video/audio rows overnight, IN PARALLEL, through
the exact same stages as the real-time path — plagiarism gate -> Kaapi grading -> feedback
-> and then delivers each result, one by one, to the outbound `feedback_results_queue`
(via `FeedbackService.process_feedback`) so the LMS picks it up.

Parallelism uses threads (the Kaapi HTTP calls are blocking `requests`), and EACH worker
thread gets its own Frappe connection — the safe way to parallelize Frappe work.

Resumable: only Pending rows are picked. A graded row becomes Completed; a failed row
becomes Failed; either way it leaves the Pending set, so a re-run continues cleanly.

Run:
  bench --site <site> run-nightly-batch --page-size 100 --concurrency 8
  # or:  bench --site <site> execute rag_service.rag_service.core.batch_runner.run_nightly_batch
"""
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List

import frappe

from ..rag_service.doctype.kaapi_settings.kaapi_settings import assessments_enabled

# Must match feedback_handler.DEFERRED_SUBMISSION_TYPES.
DEFERRED_SUBMISSION_TYPES = ["image", "text", "emoji", "video", "audio"]

def _assessment_mode() -> bool:
    return assessments_enabled()


def _media_kind(submission_type: str) -> str:
    """Collapse submission types into the ASSESSMENT config buckets."""
    submission_type = (submission_type or "").lower()
    if submission_type in ("text", "emoji"):
        return "text"
    if submission_type == "audio":
        return "audio"
    return "video" if submission_type == "video" else "image"


def _message_from_request(fr) -> Dict:
    """Rebuild a queue-style message dict from a stored Feedback Request row, so the
    existing grading stages receive exactly what they expect."""
    try:
        similar = json.loads(fr.similar_sources) if fr.similar_sources else []
    except Exception:
        similar = []
    return {
        "submission_id": fr.submission_id,
        "student_id": fr.student_id,
        "assignment_id": fr.assignment_id,
        "grade": fr.grade,
        "level": fr.level,
        "language": fr.language,
        "submission_type": fr.submission_type,
        "submission_url": fr.submission_url,
        "submission_text": fr.submission_text,
        "plagiarism_score": fr.plagiarism_score,
        "is_plagiarized": bool(fr.is_plagiarized),
        "plagiarism_source": fr.plagiarism_source,
        "match_type": fr.match_type,
        "is_ai_generated": bool(fr.is_ai_generated),
        "ai_confidence": fr.ai_confidence,
        "ai_detection_source": fr.ai_detection_source,
        "similar_sources": similar,
        "eval_type": fr.eval_type,
    }


async def _grade_request(fr_name: str) -> str:
    """Grade one Pending Feedback Request and deliver it. Reuses the real-time stages."""
    from .feedback_service import FeedbackService
    from .assignment_context_manager import AssignmentContextManager
    from ..utils.submission_data import normalize_submission_payload

    fr = frappe.get_doc("Feedback Request", fr_name)
    message_data = _message_from_request(fr)
    submission_data = normalize_submission_payload(message_data)

    assignment_context = await AssignmentContextManager(fr.source).get_assignment_context(fr.assignment_id)
    if not assignment_context:
        raise ValueError(f"No assignment context for assignment {fr.assignment_id}")
    assignment_context["student"] = {
        "student_id": fr.student_id,
        "grade": fr.grade,
        "level": fr.level,
        "language": fr.language,
    }

    fs = FeedbackService()
    feedback, model_used, template_used = await fs.generate_feedback(
        assignment_context=assignment_context,
        submission_data=submission_data,
        submission_id=fr_name,
        plagiarism_data=message_data,
        feedback_request_id=fr_name,
    )
    # Persist + publish one message to the outbound queue (LMS consumes it).
    await fs.process_feedback(fr_name, feedback, model_used, template_used)
    return "graded"


def _grade_in_thread(site: str, fr_name: str) -> str:
    """Worker: own Frappe connection + event loop, grade one request. Never raises."""
    frappe.init(site=site)
    frappe.connect()
    try:
        return asyncio.run(_grade_request(fr_name))
    except Exception as e:
        frappe.log_error(f"Nightly batch failed for {fr_name}: {e}", "Nightly Batch Error")
        try:
            from .feedback_service import FeedbackService
            FeedbackService().fail_request(fr_name, str(e))
        except Exception:
            pass
        return "failed"
    finally:
        frappe.destroy()


def _fetch_pending(page_size: int) -> List[str]:
    return frappe.get_list(
        "Feedback Request",
        # tapapp rows are submitted right away (see feedback_handler), never by the scheduled run.
        filters={"status": "Pending", "source": "whatsapp", "submission_type": ["in", DEFERRED_SUBMISSION_TYPES]},
        order_by="created_at asc",
        limit=page_size,
        pluck="name",
    )


# ---------------------------------------------------------------------------
# ASSESSMENT submit phase (Phase 1 of the two-phase Kaapi flow)
# ---------------------------------------------------------------------------
async def _deliver_flagged(fs, fr_name: str, message: Dict) -> bool:
    """Plagiarised / AI-generated rows don't need grading — deliver the canned
    feedback immediately (same as the real-time path) and skip Kaapi. Returns True
    if this row was handled here."""
    if message.get("is_ai_generated"):
        feedback = fs._create_ai_generated_feedback(message)
        await fs.process_feedback(
            fr_name, feedback, "N/A", "Feedback Template for AI Generated Submission"
        )
        return True
    if message.get("is_plagiarized"):
        feedback = fs._create_plagiarism_feedback(message)
        await fs.process_feedback(
            fr_name, feedback, "N/A", "Feedback Template for Plagiarized Submission"
        )
        return True
    return False


async def _prepare_page(page: List[str]) -> Dict:
    """Prepare one page of Pending rows for submission.

    - Flagged rows are delivered canned right here (no Kaapi).
    - Clean rows are rendered (build_eval_request) and bucketed by media type.

    Returns {"buckets": {(kind, eval_type): [items]}, "flagged": n, "failed": n} where each
    item is (fr_name, row, webhook_context). Row order within a bucket is preserved and
    becomes the batch index.
    """
    from .feedback_service import FeedbackService
    from .assignment_context_manager import AssignmentContextManager
    from ..utils.submission_data import normalize_submission_payload
    from ..feedback_utils.image_evaluation_both import ImageEvaluationGenerator
    from ..feedback_utils.text_evaluation import TextEvaluationGenerator
    from ..feedback_utils.video_evaluation import VideoEvaluationGenerator
    from ..feedback_utils.audio_evaluation import AudioEvaluationGenerator
    from .kaapi_assessment import KaapiAssessmentClient

    fs = FeedbackService()
    buckets: Dict = {"buckets": {}, "flagged": 0, "failed": 0}

    for fr_name in page:
        fr = frappe.get_doc("Feedback Request", fr_name)
        message = _message_from_request(fr)

        # 1) plagiarism / AI gating — deliver canned, skip Kaapi (never for rubric re-evaluations)
        try:
            if fr.eval_type != "rubric" and await _deliver_flagged(fs, fr_name, message):
                buckets["flagged"] += 1
                continue
        except Exception as e:
            _mark_failed(fr_name, f"flagged delivery failed: {e}")
            buckets["failed"] += 1
            continue

        # 2) render the prompt (the "before model call" half of the refactor)
        try:
            submission = normalize_submission_payload(message)
            ctx = await AssignmentContextManager(fr.source).get_assignment_context(fr.assignment_id)
            if not ctx:
                raise ValueError(f"No assignment context for {fr.assignment_id}")
            ctx["student"] = {
                "student_id": fr.student_id, "grade": fr.grade,
                "level": fr.level, "language": fr.language,
            }
            kind = _media_kind(fr.submission_type)
            gen = {
                "image": ImageEvaluationGenerator,
                "text": TextEvaluationGenerator,
                "video": VideoEvaluationGenerator,
                "audio": AudioEvaluationGenerator,
            }[kind](fs)
            req = gen.build_eval_request(ctx, submission, for_assessment=True)
            if kind == "video":
                media_url = req.get("video_url")
            elif kind == "audio":
                media_url = req.get("audio_url")
            else:
                media_url = req.get("image_url")
            row = KaapiAssessmentClient.build_row(
                fr.submission_id, req["combined_prompt"], kind, media_url,
            )
        except Exception as e:
            _mark_failed(fr_name, f"prepare failed: {e}")
            buckets["failed"] += 1
            continue

        webhook_ctx = {"template_used": req["template_used"]}
        buckets["buckets"].setdefault((kind, fr.eval_type), []).append((fr_name, row, webhook_ctx))

    return buckets


def _mark_failed(fr_name: str, error: str) -> None:
    from .feedback_service import FeedbackService
    try:
        FeedbackService().fail_request(fr_name, error, kaapi_status="Failed")
    except Exception:
        frappe.db.rollback()


def _submit_bucket(client, kind: str, eval_type: str, items: List) -> int:
    """Submit one media + eval type bucket to Kaapi and record correlation state on each row."""
    config = client.config_for(kind, eval_type)
    rows = [row for (_fr, row, _ctx) in items]
    assessment_id = client.submit(rows, config, request_metadata={"kind": kind, "eval_type": eval_type})
    for index, (fr_name, _row, webhook_ctx) in enumerate(items):
        webhook_ctx["model_used"] = config.model
        frappe.db.set_value(
            "Feedback Request", fr_name,
            {
                "kaapi_assessment_id": assessment_id,
                "kaapi_batch_index": index,
                "kaapi_status": "Submitted",
                "kaapi_context": json.dumps(webhook_ctx),
                "status": "Processing",
            },
        )
    frappe.db.commit()
    frappe.logger().info(
        f"Nightly submit: {kind}/{eval_type} batch {assessment_id} -> {len(items)} rows"
    )
    return len(items)


def run_submit_phase(page_size: int = 100, max_items: int = 0, names: List[str] = None) -> Dict:
    """Phase 1: render all Pending image/text/video/audio rows (or only `names`) and submit
    them to Kaapi in batches. Completion arrives later via the webhook (see api/kaapi_webhook.py).
    Flagged rows are delivered canned inline. Resumable: submitted rows leave the Pending set."""
    from .kaapi_assessment import KaapiAssessmentClient

    client = KaapiAssessmentClient()
    summary = {"submitted": 0, "flagged": 0, "failed": 0, "pages": 0, "total": 0}
    started = datetime.now()

    # Explicitly named rows (tapapp) are not picked by any later run, so they can't wait for a retry.
    retry_later = names is None
    if retry_later:
        # Snapshot (no limit): rows that turn Pending during this run wait for the next one.
        names = _fetch_pending(None)
    if max_items:
        names = names[:max_items]

    # Prepare page by page, but collect buckets across all pages so each
    # media/eval type is submitted together rather than once per page.
    buckets: Dict = {}
    for start in range(0, len(names), page_size):
        page = names[start:start + page_size]
        summary["pages"] += 1
        prepared = asyncio.run(_prepare_page(page))
        summary["flagged"] += prepared["flagged"]
        summary["failed"] += prepared["failed"]
        for key, items in prepared["buckets"].items():
            buckets.setdefault(key, []).extend(items)
        summary["total"] += len(page)
        frappe.logger().info(f"Nightly submit progress: {summary}")

    # Kaapi accepts at most 50 rows per request.
    for (kind, eval_type), items in buckets.items():
        for start in range(0, len(items), 50):
            chunk = items[start:start + 50]
            try:
                summary["submitted"] += _submit_bucket(client, kind, eval_type, chunk)
            except Exception as e:
                # Whole-batch submit failure: leave those rows Pending for the next run.
                frappe.log_error(f"Kaapi submit failed ({kind}/{eval_type}): {e}", "Nightly Submit Error")
                if not retry_later:
                    for fr_name, _row, _ctx in chunk:
                        _mark_failed(fr_name, f"Kaapi submit failed: {e}")

    summary["duration_s"] = round((datetime.now() - started).total_seconds(), 1)
    frappe.logger().info(f"Nightly submit complete: {summary}")
    print(f"Nightly submit complete: {summary}")
    return summary


# ---------------------------------------------------------------------------
# Legacy synchronous grade path (/llm/call) + dispatcher
# ---------------------------------------------------------------------------
def run_nightly_batch(page_size: int = 100, concurrency: int = 8, max_items: int = 0) -> Dict:
    """Use Kaapi Settings to select assessments or the legacy synchronous path."""
    if _assessment_mode():
        return run_submit_phase(page_size=page_size, max_items=max_items)
    return _run_nightly_batch_sync(page_size=page_size, concurrency=concurrency, max_items=max_items)


def _run_nightly_batch_sync(page_size: int = 100, concurrency: int = 8, max_items: int = 0) -> Dict:
    """Grade all Pending image/text/video/audio submissions in parallel, page by page.

    page_size   how many to pull per page
    concurrency how many to grade at once (threads); tune to Kaapi rate limits
    max_items   optional cap (0 = no cap) — useful for a smoke test
    """
    site = frappe.local.site
    summary = {"graded": 0, "failed": 0, "pages": 0, "total": 0}
    started = datetime.now()
    seen = set()

    while True:
        page = _fetch_pending(page_size)
        if not page:
            break
        # Safety: if a page returns only rows we've already handled (e.g. a row stuck
        # Pending), stop rather than loop forever.
        fresh = [n for n in page if n not in seen]
        if not fresh:
            frappe.logger().warning("Nightly batch: page had no fresh rows; stopping.")
            break
        seen.update(fresh)
        summary["pages"] += 1

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            results = list(ex.map(lambda n: _grade_in_thread(site, n), fresh))
        for r in results:
            summary[r] = summary.get(r, 0) + 1
        summary["total"] += len(fresh)

        frappe.logger().info(f"Nightly batch progress: {summary}")
        if max_items and summary["total"] >= max_items:
            break

    summary["duration_s"] = round((datetime.now() - started).total_seconds(), 1)
    frappe.logger().info(f"Nightly batch complete: {summary}")
    print(f"Nightly batch complete: {summary}")
    return summary


def enqueue_nightly_batch(page_size: int = 100, concurrency: int = 8) -> None:
    """Scheduler entrypoint (see hooks.scheduler_events). Enqueues the batch on the
    `long` queue with a large timeout, so a big overnight run is not cut off by the
    default background-job timeout. The batch is resumable, so a re-run continues safely."""
    frappe.enqueue(
        "rag_service.core.batch_runner.run_nightly_batch",
        queue="long",
        timeout=6 * 60 * 60,  # 6 hours
        job_name="nightly-batch",
        page_size=page_size,
        concurrency=concurrency,
    )
    frappe.logger().info("Nightly batch enqueued on the 'long' queue.")