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

## 1. Frappe migrate (adds Kaapi Settings and the kaapi_* fields)
```
bench --site <site> migrate
```

## 2. Kaapi Settings (no environment variables)
As a System Manager, open **Kaapi Settings** (`/app/kaapi-settings`) and save:

| Field | Value |
|---|---|
| Enable Assessments | Checked |
| API Key | Your Kaapi API key (encrypted Password field) |
| Base URL | `https://api.kaapi.ai` (default; omit `/api/v1`) |
| Image Config ID | `67865d5f-bc53-4708-9858-eb766132c0db` |
| Image Config Version | `3` |
| Text Config ID | `ec7cb5ec-6403-4c8c-98e9-ed6c963d506f` |
| Text Config Version | `2` |
| Callback URL | `https://<public-rag-host>/api/method/rag_service.api.kaapi_webhook.receive` |
| Webhook Secret | A strong shared secret (encrypted Password field) |
| Allow Insecure Webhook | Unchecked (local testing only) |

Register the same Webhook Secret in Kaapi's Credentials API. Kaapi signs callbacks with
`X-Webhook-Signature` and `X-Webhook-Timestamp`; the secret is never put in the URL. Configure both media types when grading images and text/emoji;
at least one config is required to enable assessments. Config versions must be positive integers.
Kaapi's configs select the grading model/provider; assessments do not need an LLM Settings row.
Existing RAG, RabbitMQ, GCS and prompt settings still serve their existing purposes.

For existing deployments, copy the old environment values into this DocType before resuming
the scheduler after migration. Preserve the existing Webhook Secret while batches await results.
`KAAPI_*`, `RAG_EVAL_MODE`, `RAG_EVAL_PROVIDER` and `RAG_WEBHOOK_ALLOW_INSECURE` no longer
configure assessments. Enabled assessments also ensure day-time deferral regardless of
`RAG_BATCH_MODE`. Settings are site-specific and read by both workers and the web process;
no environment setup or restart is needed when changing settings. Restart existing workers
once when deploying this code so they load the new implementation.

## 3. Expose the webhook (infra)
- [ ] Public HTTPS route to `/api/method/rag_service.api.kaapi_webhook.receive`, reachable
      from Kaapi's servers (through the LB/ingress).
- [ ] Endpoint is guest-callable (already `@frappe.whitelist(allow_guest=True)`); it is
      protected by Kaapi's HMAC-SHA256 signature. Leave **Allow Insecure Webhook** unchecked.
- [ ] Confirm **Callback URL** points to the bare public endpoint and contains no secret.

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
      `run_nightly_batch` → dispatches to `run_submit_phase` when **Enable Assessments** is checked.
- [ ] Add a "stuck row" sweeper (rows in `kaapi_status=Submitted` beyond N hours →
      re-submit / alert), since results are webhook-only and can't be polled. (Open item #5.)

## 6. Verify at scale (open items #4/#5)
- [ ] Kaapi `/assessments` batch-size / rate limits → tune `page_size`.
- [ ] Kaapi webhook retry policy → confirms idempotency is enough (it is: only `Submitted`
      rows are processed).

## 7. Rollback
- Uncheck **Enable Assessments** in Kaapi Settings → `batch_runner` reverts to the synchronous
  `/llm/call` path, which still uses its existing LLM Settings/provider configuration.
  Keep Webhook Secret unchanged so already-submitted batches can finish.

## Notes / gotchas baked into the code
- Correlation is by echoed `submission_id` (id-based, order-independent); `kaapi_batch_index` is
  audit metadata only. A failed row = `output.assessment == null` with no id (NOT `counts.errors`)
  and is caught by elimination (rows still `Submitted` after all items processed → `Failed`,
  gated on terminal batch status + full item set).
- Rendered prompt carries the rubric/description/level/language; literal `{…}` braces in it
  pass through Kaapi untouched (validated) — no escaping.
- Flagged (plagiarised/AI) rows are delivered canned inline during submit; they never go to Kaapi.
