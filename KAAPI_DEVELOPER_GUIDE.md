# Kaapi ASSESSMENT integration — developer guide

**Branch:** `feat/kaapi-llm-eval` · **Date:** 2026-09-05
How the Kaapi ASSESSMENT (batch + webhook) grading path is built and implemented. Read
`KAAPI_USER_FLOW.md` first for the behavior; this doc is the code-level companion.
See also `KAAPI_ASSESSMENT_SWITCH_SCOPE.md` (design/decisions) and `KAAPI_DEPLOY_CHECKLIST.md`.

---

## 1. Why the architecture is shaped this way

Kaapi's ASSESSMENT API is **asynchronous and webhook-only**:
- You submit a batch (`POST /assessments`) and get back an `assessment_id`.
- **There is no GET/poll endpoint** — results are delivered *only* by Kaapi POSTing to a
  `callback_url` you provide.

The old grading path was **synchronous**: grade an image, wait inline for the answer, deliver.
That's impossible here (the grade arrives later, on a separate inbound request). So grading is
split into **two phases**:

```
PHASE 1 (submit):   render prompts -> POST /assessments (batch) with callback_url -> mark Submitted -> stop
PHASE 2 (webhook):  Kaapi POSTs results -> correlate -> finalize -> deliver to LMS queue
```

Design principle: **keep rag_service's entire prompt/rubric/feedback engine; swap only the LLM
transport.** rag_service renders the same prompt it always did and ships it to Kaapi as an input;
Kaapi returns the same JSON shape; the existing post-processing runs unchanged.

---

## 2. Code map

| File | Responsibility |
|---|---|
| `feedback_utils/evaluation_generation.py` | Base `EvaluationGenerator`. Added `_prepare_eval()` (before model call) and `finalize_feedback()` (after model call). |
| `feedback_utils/image_evaluation_both.py` | `ImageEvaluationGenerator.build_eval_request()` + refactored `generate_feedback()`. |
| `feedback_utils/text_evaluation.py` | `TextEvaluationGenerator.build_eval_request()` + refactored `generate_feedback()`. |
| `core/kaapi_assessment.py` | **NEW.** `KaapiAssessmentClient` — the submit-side API client. |
| `core/batch_runner.py` | Nightly batch. `run_submit_phase` (Phase 1) + `RAG_EVAL_MODE` dispatch; legacy sync path kept. |
| `api/kaapi_webhook.py` | **NEW.** `receive()` (whitelisted endpoint) + `process_payload()` (Phase 2 logic). |
| `core/feedback_handler.py` | Day-time deferral (image/text → `Pending`); unchanged by this work beyond earlier deferral. |
| `core/feedback_service.py` | `process_feedback()` — delivery to the LMS queue (reused unchanged). |
| `doctype/feedback_request/feedback_request.json` | +4 `kaapi_*` fields. |

---

## 3. The refactor: splitting the eval "seam"

Historically the image/text evaluators did everything in one method: resolve provider → fetch
template → render prompt → **call the model** → parse → attach defaults → return feedback. To let
the submit phase and the webhook each use one half, this was split in the base class:

```python
# evaluation_generation.py (EvaluationGenerator)

def _prepare_eval(self, assignment_context, submission_data, media_type) -> dict:
    # BEFORE the model call: provider, template, rendered prompt.
    # returns {provider_name, llm_provider, model_used, template, template_used,
    #          expected_format, system_prompt, formatted_user_prompt, combined_prompt}

def finalize_feedback(self, raw_text, expected_format, cost, log_prob=None) -> dict:
    # AFTER the model call: parse -> plagiarism defaults -> default fields -> strengths markers
```

Each evaluator exposes a thin `build_eval_request()`:

```python
# image_evaluation_both.py
def build_eval_request(self, assignment_context, submission_data):
    req = self._prepare_eval(assignment_context, submission_data, "image")
    req["image_url"] = submission_data.get("submission_url")   # Kaapi reads gs:// directly
    return req
```

`generate_feedback()` now = `build_eval_request()` → model call → `finalize_feedback()`. Behavior
is byte-identical (bench-verified), so the real-time path and rollback are untouched. The submit
phase calls `build_eval_request()` (no model call); the webhook calls `finalize_feedback()` on
Kaapi's returned JSON.

---

## 4. Phase 1 — submit (`core/kaapi_assessment.py` + `core/batch_runner.py`)

### KaapiAssessmentClient
```python
client = KaapiAssessmentClient()
assessment_id = client.submit(rows, media_type, request_metadata={"kind": media_type})
```
- Auth: `X-API-KEY: ApiKey <key>` (key from `KAAPI_API_KEY` or LLM Settings provider=Kaapi).
- `_config_for(media_type)` → `(config_id, version)` from env
  (`KAAPI_IMAGE_CONFIG_*` / `KAAPI_TEXT_CONFIG_*`).
- `submit()` POSTs `{config:{id,version}, input:{data:rows}, callback_url, request_metadata}`,
  returns `data.assessment_id`. Requires `KAAPI_CALLBACK_URL` (webhook-only).
- Each row: `{submission_id, rendered_prompt[, artwork_image]}` — shaped to the config's
  `input_schema`.

### batch_runner
```python
def run_nightly_batch(...):          # dispatcher
    if _assessment_mode():           # RAG_EVAL_MODE == "assessment"
        return run_submit_phase(...)
    return _run_nightly_batch_sync(...)   # legacy /llm/call path (rollback)
```

`run_submit_phase` per page of `Pending` rows (`_prepare_page`):
1. **Flagged rows** (plagiarised/AI) → `_deliver_flagged()` generates canned feedback and calls
   `process_feedback()` inline. Never sent to Kaapi.
2. **Clean rows** → `build_eval_request()` renders the prompt; bucketed by `_media_kind()` into
   `image` / `text`.
3. `_submit_bucket()` submits each bucket and writes correlation state on every row:
   `kaapi_assessment_id`, `kaapi_batch_index` (audit), `kaapi_status="Submitted"`,
   `kaapi_context` (JSON: `expected_format`, `model_used`, `template_used` — so the webhook can
   finalize without re-deriving), `status="Processing"`.

Resumable: submitted rows leave the `Pending` set; a re-run continues cleanly.

---

## 5. Phase 2 — webhook (`api/kaapi_webhook.py`)

`receive()` is the whitelisted endpoint (auto-exposed by Frappe at
`/api/method/rag_service.api.kaapi_webhook.receive`):

```python
@frappe.whitelist(allow_guest=True)
def receive():
    if not _verify_secret(): return 401
    payload = _raw_body()
    return process_payload(payload)
```

- `_verify_secret()` — compares `?token=` (or `X-Webhook-Secret`) to `KAAPI_WEBHOOK_SECRET`.
  `RAG_WEBHOOK_ALLOW_INSECURE=1` bypasses (local testing only).
- `_extract(payload)` → `(assessment_id, items, total_items, status)`, tolerating the
  `{success, data:{...}}` envelope or the inner object.

### Correlation algorithm (`process_payload`) — id-based, NOT positional
```
load rows where kaapi_assessment_id == aid AND kaapi_status == "Submitted"  -> expected{name: row}
for each item with a non-null assessment:
    sid = assessment.submission_id                 # echoed id
    row = expected.pop(sid)                         # match by id (order-independent)
    if row: finalize_feedback -> process_feedback -> deliver; mark Completed
    else:   skip (idempotent retry / unknown id)
# elimination: whatever remains in `expected` did not grade
if status in (COMPLETED, FAILED) and len(items) >= total_items:
    mark remaining rows Failed
```

Why:
- **Successful rows echo `submission_id`** (added to the config's output schema) → match by id.
- **Failed rows echo no id** (`assessment` is `null`) → caught by **elimination** (rows left
  awaiting a result). Gated on terminal status + full item set so a partial callback can't
  false-fail rows.
- **Idempotent** for free: only `Submitted` rows are loaded; a replay finds nothing to do.
- **Fail-safe:** a wrong/hallucinated id matches no awaiting row → skipped, the intended row is
  re-graded next batch — never mis-assigned.

`_finalize_and_deliver()` builds `raw_text = json.dumps(assessment)`, calls
`finalize_feedback(raw_text, expected_format, cost=0.0, log_prob=None)` (the ASSESSMENT API gives
no per-item cost/logprob), then `process_feedback()` (delivery), then sets `kaapi_status=Completed`.

---

## 6. Data model (`Feedback Request`)
| Field | Type | Purpose |
|---|---|---|
| `kaapi_assessment_id` | Data | Batch id; groups rows for webhook correlation |
| `kaapi_batch_index` | Int | Row's position in the submitted batch — **audit only** (not used for correlation) |
| `kaapi_status` | Select (Submitted/Completed/Failed) | Kaapi lifecycle; drives idempotency + the stuck-row sweeper |
| `kaapi_context` | Long Text (JSON) | `{expected_format, model_used, template_used}` captured at submit for the webhook |

Apply with `bench --site <site> migrate`.

---

## 7. Kaapi configs (created via API/Postman, ids in the deploy checklist)

Thin generic configs — one per media type. The rubric/description/level/language ride **inside**
the `rendered_prompt` input, so a single config serves every assignment.

- **input_schema:** image = `{submission_id, rendered_prompt, artwork_image(image/url)}`;
  text = `{submission_id, rendered_prompt}`.
- **submission template:** `"{rendered_prompt}\n\nReturn submission_id exactly as: {submission_id}"`.
- **json_output_schema:** mirrors the prompt template's `response_format`
  (`rubric_evaluations[]` of `{Skill, grade_value, observation}`, `overall_feedback`,
  `overall_feedback_translated`, `final_grade`) **plus** an echoed `submission_id`. OpenAI strict
  mode: every property in `required`, `additionalProperties: false`; arrays may be variable length.

Config bodies live in `Desktop/RRM/kaapi_config_{image,text}_prod.json`. Current ids:
`tap-image-grader-prod = 67865d5f-…`, `tap-text-grader-prod = ec7cb5ec-…` (both v1).

---

## 8. Configuration (env)
| Var | Meaning |
|---|---|
| `RAG_EVAL_MODE` | `assessment` = new flow; else legacy `/llm/call` |
| `RAG_EVAL_PROVIDER` | prompt-render provider (default `Kaapi`) |
| `KAAPI_API_KEY` | API key (else LLM Settings provider=Kaapi) |
| `KAAPI_BASE_URL` | default `https://api.kaapi.ai` |
| `KAAPI_IMAGE_CONFIG_ID` / `_VERSION` | image config |
| `KAAPI_TEXT_CONFIG_ID` / `_VERSION` | text config |
| `KAAPI_CALLBACK_URL` | public webhook URL incl. `?token=<secret>` |
| `KAAPI_WEBHOOK_SECRET` | must equal the token in the callback URL |
| `RAG_WEBHOOK_ALLOW_INSECURE` | `1` bypasses secret (local only) |

---

## 9. Kaapi API quirks (learned the hard way — don't regress these)
- **Auth header:** `X-API-KEY: ApiKey <key>` (not `Bearer`, not bare key).
- **No poll endpoint:** results are webhook-only; `callback_url` is mandatory.
- **Positional-but-id-less failures:** a failed row returns `output.assessment == null` with
  NO echoed id; `item.error` stays `null` and `counts.errors` stays `0`. Detect failure via
  `assessment is null`, never `counts.errors`.
- **OpenAI strict schema:** `required` must list every property + `additionalProperties: false`,
  else the whole batch fails silently (null outputs).
- **Braces in the rendered prompt** (`{Skill…}`) pass through Kaapi's `{var}` substitution
  untouched — no escaping needed (validated).

---

## 10. Running / testing locally
- Bench: `docker compose up -d` in `rag-bench`; site `rag.localhost`; app at
  `/home/frappe/frappe-bench/apps/rag_service` (sync edits from the mounted source when testing).
- Migrate after doctype changes: `bench --site rag.localhost migrate`.
- Run submit phase: `bench --site rag.localhost execute rag_service.core.batch_runner.run_submit_phase`.
- Webhook over HTTP: start `bench serve --port 8000` (with `KAAPI_WEBHOOK_SECRET` in env), then
  `POST` a Kaapi payload to `/api/method/rag_service.api.kaapi_webhook.receive?token=<secret>`.
- Verified end-to-end on the bench with real `submissions.csv` images: 12 rows → real Kaapi
  grade → real webhook payload → HTTP POST to `receive()` → all delivered (401 on bad token).

---

## 11. Extending
- **New media type:** add a `_media_kind()` case, an evaluator with `build_eval_request()`, a
  Kaapi config, and `KAAPI_<TYPE>_CONFIG_*` env.
- **Change feedback shape:** update the prompt template `response_format` AND the config's
  `json_output_schema` together (they must match).
- **Stuck-row sweeper (TODO):** scheduled job → rows `kaapi_status=Submitted` older than N hours
  → re-submit / alert. Required because results can't be polled.

---

## 12. Rollback
`RAG_EVAL_MODE=llm_call` (or unset) → `run_nightly_batch` reverts to the synchronous
`/llm/call` grade-and-deliver path. No code change, no logic redeploy.
