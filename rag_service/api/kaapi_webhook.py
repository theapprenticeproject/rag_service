# rag_service/rag_service/api/kaapi_webhook.py
"""
Kaapi ASSESSMENT webhook receiver — the RECEIVE side of the two-phase grading flow.

Kaapi grades a submitted batch (see core/kaapi_assessment.py) and POSTs the results
here. This endpoint correlates each result back to its Feedback Request and runs the
SAME post-processing as the real-time path (finalize_feedback -> process_feedback ->
deliver to the LMS queue).

Endpoint (whitelisted, guest-callable so Kaapi can reach it):
    POST /api/method/rag_service.api.kaapi_webhook.receive

Security: results are webhook-only and Kaapi does not send a custom auth header, so we
protect the endpoint with a shared secret carried in the callback URL (query param
`token`) and/or an `X-Webhook-Secret` header. Set KAAPI_WEBHOOK_SECRET and put the same
token in KAAPI_CALLBACK_URL. Set RAG_WEBHOOK_ALLOW_INSECURE=1 to bypass (local testing).

Correlation (verified against the live API — see KAAPI_ASSESSMENT_SWITCH_SCOPE.md):
- Results are POSITIONAL: items[i] <-> the row submitted at kaapi_batch_index i.
- Successful rows echo output.assessment.submission_id (= Feedback Request name); we use
  it to VALIDATE the positional match. Failed rows return output.assessment = null with
  no id, so position is the primary key.
- Failure of a single row shows up as output.assessment == null (NOT item.error, NOT
  counts.errors). Such rows are marked Failed and can be re-submitted next batch.
"""
import json
import os
import asyncio
from typing import Dict, List, Optional, Tuple

import frappe


def _verify_secret() -> bool:
    if os.environ.get("RAG_WEBHOOK_ALLOW_INSECURE", "").strip() in ("1", "true", "yes"):
        return True
    secret = os.environ.get("KAAPI_WEBHOOK_SECRET", "").strip()
    if not secret:
        # No secret configured and insecure not explicitly allowed -> refuse.
        return False
    provided = ""
    try:
        provided = (frappe.request.args.get("token")
                    or frappe.get_request_header("X-Webhook-Secret")
                    or "")
    except Exception:
        provided = ""
    return provided == secret


def _raw_body() -> Dict:
    try:
        raw = frappe.request.get_data(as_text=True)
    except Exception:
        raw = None
    if not raw:
        # Fallback: Frappe may have parsed it into form_dict.
        fd = dict(frappe.local.form_dict or {})
        fd.pop("cmd", None)
        return fd
    return json.loads(raw)


def _extract(payload: Dict) -> Tuple[Optional[str], List[Dict], Optional[int], Optional[str]]:
    """Pull (assessment_id, items, total_items, status) out of the callback body,
    tolerating either the full `{success, data:{...}}` envelope or the inner data object."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    assessment_id = data.get("assessment_id") or payload.get("assessment_id")
    status = data.get("status") or payload.get("status")
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    items = inner.get("items") or []
    total_items = inner.get("total_items")
    return assessment_id, items, total_items, status


def _finalize_and_deliver(fs, gen, fr_name: str, assessment: Dict, webhook_ctx: Dict) -> None:
    """Run the shared post-processing on one graded item and deliver it to the LMS."""
    expected_format = webhook_ctx.get("expected_format") or {}
    model_used = webhook_ctx.get("model_used") or "Kaapi"
    template_used = webhook_ctx.get("template_used") or "Kaapi Assessment"

    # finalize_feedback parses a raw JSON string; the ASSESSMENT API gives no per-item
    # cost/logprob, so those markers are 0.
    raw_text = json.dumps(assessment)
    feedback = gen.finalize_feedback(raw_text, expected_format, cost=0.0, log_prob=None)

    asyncio.run(fs.process_feedback(fr_name, feedback, model_used, template_used))
    frappe.db.set_value("Feedback Request", fr_name, {"kaapi_status": "Completed"})
    frappe.db.commit()


@frappe.whitelist(allow_guest=True)
def receive():
    """Receive a Kaapi ASSESSMENT result batch and finish each submission."""
    if not _verify_secret():
        frappe.local.response["http_status_code"] = 401
        return {"ok": False, "error": "unauthorized"}

    try:
        payload = _raw_body()
    except Exception as e:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": f"bad body: {e}"}

    result = process_payload(payload)
    if not result.get("ok"):
        frappe.local.response["http_status_code"] = 400
    return result


def process_payload(payload: Dict) -> Dict:
    """Correlate a Kaapi result batch to its Feedback Requests and finish each one,
    matching each result by its echoed `submission_id`. Split out from `receive()` so it
    can be driven directly (tests, replays) without an HTTP request context.

    Correlation is by id, NOT position: a graded item is matched to the row whose name
    equals its echoed `output.assessment.submission_id`. Failed rows echo no id (their
    `assessment` is null), so they're identified by ELIMINATION — any row still awaiting a
    result whose id never came back in a successful item did not grade. Elimination only
    runs when the batch is terminal AND this callback carries the whole item set, so a
    partial/early callback can't false-fail rows.
    """
    assessment_id, items, total_items, status = _extract(payload)
    if not assessment_id:
        return {"ok": False, "error": "no assessment_id in payload"}

    # Only rows still awaiting a result — this also makes the handler idempotent on retries
    # (already-Completed/Failed rows are simply not in the set).
    rows = frappe.get_all(
        "Feedback Request",
        filters={"kaapi_assessment_id": assessment_id, "kaapi_status": "Submitted"},
        fields=["name", "kaapi_context"],
    )
    expected = {r.name: r for r in rows}

    if total_items is not None and len(items) != total_items:
        frappe.log_error(
            f"Kaapi webhook length mismatch for {assessment_id}: "
            f"items={len(items)} total_items={total_items}",
            "Kaapi Webhook Mismatch",
        )

    from ..core.feedback_service import FeedbackService
    from ..feedback_utils.evaluation_generation import EvaluationGenerator

    fs = FeedbackService()
    gen = EvaluationGenerator(fs)

    summary = {"completed": 0, "failed": 0, "skipped": 0, "unmatched": 0}

    for item in items:
        assessment = ((item.get("output") or {}).get("assessment")) if isinstance(item, dict) else None

        # Failed items carry no id (assessment is null) — left in `expected`, handled by
        # elimination below.
        if not assessment:
            continue

        sid = assessment.get("submission_id")
        if not sid:
            # Graded but no id echoed — cannot be matched by id. Fail-safe: leave the
            # intended row to elimination rather than guess.
            summary["unmatched"] += 1
            frappe.log_error(
                f"Kaapi webhook: graded item with no submission_id in {assessment_id}",
                "Kaapi Webhook Unmatched",
            )
            continue

        row = expected.pop(sid, None)
        if not row:
            # id isn't awaiting a result: already handled (idempotent retry) or unknown id.
            summary["skipped"] += 1
            continue

        try:
            webhook_ctx = json.loads(row.kaapi_context or "{}")
        except Exception:
            webhook_ctx = {}

        try:
            _finalize_and_deliver(fs, gen, row.name, assessment, webhook_ctx)
            summary["completed"] += 1
        except Exception as e:
            frappe.db.rollback()
            frappe.db.set_value(
                "Feedback Request", row.name,
                {"status": "Failed", "kaapi_status": "Failed", "error_log": str(e)[:1000]},
            )
            frappe.db.commit()
            summary["failed"] += 1
            frappe.log_error(
                f"Kaapi webhook finalize failed for {row.name}: {e}",
                "Kaapi Webhook Finalize Error",
            )

    # Elimination: rows still awaiting a result did not grade (null assessment / dropped /
    # unmatched id). Only conclude this on a terminal batch whose callback carries the full
    # item set, so a partial or still-processing callback can't false-fail rows.
    terminal = str(status).upper() in ("COMPLETED", "FAILED")
    full_batch = (total_items is None) or (len(items) >= total_items)
    if terminal and full_batch:
        for name in list(expected.keys()):
            frappe.db.set_value(
                "Feedback Request", name,
                {"status": "Failed", "kaapi_status": "Failed",
                 "error_log": f"No successful Kaapi result for this row "
                              f"(batch {assessment_id}, status {status})"[:1000]},
            )
            frappe.db.commit()
            summary["failed"] += 1

    frappe.logger().info(f"Kaapi webhook {assessment_id} processed: {summary}")
    return {"ok": True, "assessment_id": assessment_id, "status": status, **summary}
