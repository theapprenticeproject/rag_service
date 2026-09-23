# Kaapi ASSESSMENT switch — deploy checklist

**Branch:** `feat/kaapi-llm-eval` · **Date:** 2026-09-04
Companion to `KAAPI_ASSESSMENT_SWITCH_SCOPE.md`. Everything below is built and bench-tested
(including a full Kaapi→our-endpoint run via a cloudflared tunnel); this is the wire-up to run
it against the real Kaapi + LMS.

## 0. Prerequisites (already done)
- [x] Code merged: `core/kaapi_assessment.py`, `api/kaapi_webhook.py`,
      `core/batch_runner.py` (submit phase), `feedback_utils/*` (refactor),
      `feedback_request.json` (+4 kaapi fields).
- [x] Production Kaapi configs created + validated:
  - image: `tap-image-grader-prod` = `67865d5f-bc53-4708-9858-eb766132c0db` (v1)
  - text:  `tap-text-grader-prod`  = `ec7cb5ec-6403-4c8c-98e9-ed6c963d506f` (v1)
- [x] google-gcp credential registered in Kaapi (private-bucket reads).

## 1. Frappe migrate (adds the 4 kaapi_* fields)
```
bench --site <site> migrate
```

## 2. Environment / settings
Set on the rag_service app environment (env or a settings doctype):
```
RAG_EVAL_MODE=assessment           # flips batch_runner to the submit phase
RAG_EVAL_PROVIDER=Kaapi            # keep as-is (prompt rendering path)
KAAPI_API_KEY=<key>                # or LLM Settings provider=Kaapi
KAAPI_BASE_URL=https://api.kaapi.ai
KAAPI_IMAGE_CONFIG_ID=67865d5f-bc53-4708-9858-eb766132c0db
KAAPI_IMAGE_CONFIG_VERSION=1
KAAPI_TEXT_CONFIG_ID=ec7cb5ec-6403-4c8c-98e9-ed6c963d506f
KAAPI_TEXT_CONFIG_VERSION=1
KAAPI_CALLBACK_URL=https://<public-rag-host>/api/method/rag_service.api.kaapi_webhook.receive?token=<secret>
KAAPI_WEBHOOK_SECRET=<secret>      # must equal the token in KAAPI_CALLBACK_URL
```

## 3. Expose the webhook (infra)
- [ ] Public HTTPS route to `/api/method/rag_service.api.kaapi_webhook.receive`, reachable
      from Kaapi's servers (through the LB/ingress).
- [ ] Endpoint is guest-callable (already `@frappe.whitelist(allow_guest=True)`); it is
      protected by the `?token=` secret. Do NOT set `RAG_WEBHOOK_ALLOW_INSECURE`.
- [ ] Confirm the public URL matches `KAAPI_CALLBACK_URL` exactly (incl. the token).

## 4. Smoke test on staging
- [ ] Seed one Pending image + one Pending text Feedback Request (or let the normal
      day-time deferral create them).
- [ ] Run the submit phase:
      `bench --site <site> execute rag_service.core.batch_runner.run_submit_phase`
      → rows go `Processing` / `kaapi_status=Submitted`, an `assessment_id` is stored.
- [ ] Wait for Kaapi → webhook fires → rows become `Completed`, feedback delivered to the
      `feedback_results` LMS queue. Check `kaapi_status=Completed`.
- [ ] Force one failure (bad image URL) → that row ends `Failed`, others deliver.

## 5. Schedule
- [x] Nightly cron already enqueues `enqueue_nightly_batch` (hooks.py, 01:00) →
      `run_nightly_batch` → dispatches to `run_submit_phase` when `RAG_EVAL_MODE=assessment`.
- [ ] Add a "stuck row" sweeper (rows in `kaapi_status=Submitted` beyond N hours →
      re-submit / alert), since results are webhook-only and can't be polled. (Open item #5.)

## 6. Verify at scale (open items #4/#5)
- [ ] Kaapi `/assessments` batch-size / rate limits → tune `page_size`.
- [ ] Kaapi webhook retry policy → confirms idempotency is enough (it is: only `Submitted`
      rows are processed).

## 7. Rollback
- Set `RAG_EVAL_MODE=llm_call` (or unset) → `batch_runner` reverts to the synchronous
  `/llm/call` path. No code change, no redeploy of logic.

## Notes / gotchas baked into the code
- Correlation is by echoed `submission_id` (id-based, order-independent); `kaapi_batch_index` is
  audit metadata only. A failed row = `output.assessment == null` with no id (NOT `counts.errors`)
  and is caught by elimination (rows still `Submitted` after all items processed → `Failed`,
  gated on terminal batch status + full item set).
- Rendered prompt carries the rubric/description/level/language; literal `{…}` braces in it
  pass through Kaapi untouched (validated) — no escaping.
- Flagged (plagiarised/AI) rows are delivered canned inline during submit; they never go to Kaapi.
