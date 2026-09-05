# Kaapi platform setup via Postman / API (credentials + ASSESSMENT integration)

**Branch:** `feat/kaapi-llm-eval` · **Date:** 2026-09-07
How the Kaapi side was set up by hand (Postman/API): auth, the **google-gcp service-account
credential**, the **ASSESSMENT config + version** management, and **submitting assessments**.
Keep this for re-provisioning or onboarding. Concept/code lives in `KAAPI_DEVELOPER_GUIDE.md`.

> ⚠️ Never paste real keys into shared docs/chat. Use placeholders throughout.

---

## 0. Basics

- **Base URL (production):** `https://api.kaapi.ai` — API routes under `/api/v1`.
  - Staging is `https://api-staging.kaapi.ai`. The sample Postman collection defaults to staging;
    **switch it to production** (our key validates against production).
- **Auth header (non-obvious):**
  ```
  X-API-KEY: ApiKey <YOUR_KEY>
  ```
  Note the literal `ApiKey ` prefix. NOT `Authorization: Bearer`, NOT a bare key.
  - Wrong prefix → `401 "Invalid Authorization format"`.
  - Right prefix, wrong host/key → `403 "Could not validate credentials"`.
- **Response envelope:** `{ "success": bool, "data": ..., "error": ..., "errors": ..., "metadata": ... }`.

### Postman setup
1. Import the collection Kaapi sent.
2. In the environment, add a variable `API_KEY` with value **`ApiKey <YOUR_KEY>`** (include the
   prefix in the variable, so the Auth tab can use `{{API_KEY}}` directly as the `X-API-KEY` value).
3. Set the collection/environment base URL to `https://api.kaapi.ai`.
4. Each request's Auth: header `X-API-KEY = {{API_KEY}}`.

---

## 1. Register the GCS service-account credential (private-bucket reads)

Lets Kaapi read images from a **private** GCS bucket directly via `gs://` URLs (instead of
short-lived signed URLs). One-time.

**Request:** `POST https://api.kaapi.ai/api/v1/credentials`
**Body (raw JSON):**
```json
{
  "provider": "google-gcp",
  "name": "tap-gcs-readonly",
  "credentials": {
    "api_key": "<GCP_API_KEY>",
    "project_id": "<GCP_PROJECT_ID>",
    "location": "us-central1",
    "gcs_bucket": "assignment_submission_tap",
    "sa_key": { "...paste the service-account JSON object here..." }
  }
}
```
Notes:
- `sa_key` is the **service-account key JSON** (the file downloaded from GCP). Paste it as a JSON
  object (or the whole thing as a string if the collection template expects a string).
- `api_key` is a GCP API key (was a blocker until the team provided it — a 422
  `Missing required fields for google-gcp: api_key` means it's absent).
- **Read-only scoping is on YOU, not the request.** The `name`/`gcs_bucket` fields don't limit
  anything — Kaapi gets whatever the SA's IAM allows. Give Kaapi a **dedicated SA** with only
  `roles/storage.objectViewer` on `assignment_submission_tap` (no admin/editor roles).
- After registering, submit assessments with `gs://assignment_submission_tap/...` URLs and Kaapi
  resolves them via this credential.

---

## 2. Config & version management

A **config** is a saved grading template (fixed instructions + input schema + output schema).
The rubric/level/language are NOT in the config — they ride inside the `rendered_prompt` input,
so one config per media type serves every assignment.

### 2a. Create a config
**Request:** `POST /api/v1/configs`
**Body:** the full config (bodies saved in `Desktop/RRM/kaapi_config_{image,text}_prod.json`):
```json
{
  "name": "tap-image-grader-prod",
  "description": "...",
  "tag": "ASSESSMENT",
  "commit_message": "initial production image config",
  "config_blob": {
    "input_schema": {
      "submission_id":   { "type": "text" },
      "rendered_prompt": { "type": "text" },
      "artwork_image":   { "type": "image", "format": "url" }
    },
    "assessment": {
      "provider": "openai",
      "params": {
        "model": "gpt-4o",
        "instructions": "Execute the grading prompt in the submission against the image. Copy the given submission_id verbatim into the submission_id output field. Return output strictly matching the JSON schema.",
        "submission": "{rendered_prompt}\n\nReturn submission_id exactly as: {submission_id}",
        "json_output_schema": { "...see 2d..." }
      }
    }
  }
}
```
- Returns a `config_id` + `version: 1`.
- `name` must be unique per project (`Config with name '…' already exists` otherwise).
- The **text** config is identical but drops `artwork_image` from `input_schema`.

### 2b. Add a new version (when the schema/prompt changes)
`POST /api/v1/configs/{config_id}/versions` with `{ "commit_message": "...", "config_blob": {...} }`
→ returns `version: 2`, etc. Submit against the version you want.

### 2c. Update config (metadata only)
`PATCH`/update on the config only edits `name`/`description` — **it does NOT change the
`config_blob`.** To change the grading logic/schema, create a new **version** (2b).

### 2d. `json_output_schema` — OpenAI strict-mode rules (learned the hard way)
Must mirror the prompt template's `response_format` **plus** an echoed `submission_id`:
```json
{
  "type": "object",
  "properties": {
    "submission_id": { "type": "string", "description": "echo the input submission_id exactly" },
    "rubric_evaluations": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "Skill":       { "type": "string" },
          "grade_value": { "type": "integer" },
          "observation": { "type": "string" }
        },
        "required": ["Skill", "grade_value", "observation"],
        "additionalProperties": false
      }
    },
    "overall_feedback":            { "type": "string" },
    "overall_feedback_translated": { "type": "string" },
    "final_grade":                 { "type": "integer" }
  },
  "required": ["submission_id","rubric_evaluations","overall_feedback","overall_feedback_translated","final_grade"],
  "additionalProperties": false
}
```
Strict-mode rules OpenAI enforces (violate → the whole batch fails **silently** with null outputs):
- **Every** key in `properties` must appear in `required`.
- Every object needs `"additionalProperties": false`.
- Arrays may be variable length (fixed item shape) — this is how one schema handles a 2-skill
  Arts rubric and a 4-skill Science rubric.
- The `submission_id` echo makes results correlatable (successful rows echo it; instruct the
  model to copy it verbatim).

---

## 3. Submit an assessment (batch grading)

**Request:** `POST /api/v1/assessments`
**Body:**
```json
{
  "config": { "id": "<config_id>", "version": 1 },
  "input": {
    "data": [
      { "submission_id": "ROW-1", "rendered_prompt": "<full grading prompt>", "artwork_image": "gs://assignment_submission_tap/x.jpg" }
    ]
  },
  "callback_url": "https://<public-host>/api/method/rag_service.api.kaapi_webhook.receive?token=<secret>",
  "request_metadata": { "batch": "whatever" }
}
```
- Returns `{ assessment_id, status: "PROCESSING" }`.
- **`callback_url` is mandatory** — results are webhook-only (see §4).
- One row = one `input.data` entry; each must match the config's `input_schema`.

### For testing without a public host
- Point `callback_url` at a **webhook.site** inbox to capture the raw result payload; retrieve it
  via `GET https://webhook.site/token/<uuid>/requests`.
- Or run a **cloudflared** quick tunnel to a local server so Kaapi reaches your endpoint directly:
  `cloudflared tunnel --url http://localhost:8000 --http-host-header rag.localhost` (local aid
  only — production uses the real domain).

---

## 4. Getting results — webhook-only

- **There is NO GET/poll endpoint.** `GET /assessments/{id}` returns `404`. Results are delivered
  **only** by Kaapi POSTing to your `callback_url`.
- The callback body:
  ```json
  { "success": true, "data": { "assessment_id": "...", "status": "COMPLETED",
    "data": { "total_items": N, "counts": {"assessed": N, "filtered": 0, "errors": 0},
              "items": [ { "output": { "assessment": {...} }, "error": null }, ... ] } } }
  ```
- **Per-row failure:** shows up as `output.assessment == null` (NOT `item.error`, NOT
  `counts.errors` — those stay null/0). Failed rows echo **no** `submission_id`.
- Correlate successful rows by echoed `submission_id`; treat `assessment == null` as failed.

---

## 5. Gotchas checklist (things that cost us time)
- [ ] Header is `X-API-KEY: ApiKey <key>` (with the prefix).
- [ ] Use **production** `api.kaapi.ai`, not the collection's default staging host.
- [ ] ASSESSMENT feature must be **enabled** for the API key (was disabled early on).
- [ ] `/credentials` google-gcp needs `api_key` + `sa_key` + `project_id` + `location` + `gcs_bucket`.
- [ ] `json_output_schema`: all properties in `required` + `additionalProperties:false`, else the
      batch fails silently (null outputs).
- [ ] `callback_url` required; no polling. Match it exactly (bare endpoint, correct token).
- [ ] Literal `{…}` braces inside `rendered_prompt` pass through Kaapi's `{var}` substitution
      untouched — no escaping needed (validated).

---

## 6. Reference values from this integration
| Thing | Value |
|---|---|
| Base URL | `https://api.kaapi.ai` |
| GCS credential name | `tap-gcs-readonly` (google-gcp, bucket `assignment_submission_tap`) |
| Image config | `tap-image-grader-prod` = `67865d5f-bc53-4708-9858-eb766132c0db` (v1) |
| Text config | `tap-text-grader-prod` = `ec7cb5ec-6403-4c8c-98e9-ed6c963d506f` (v1) |
| Config bodies | `Desktop/RRM/kaapi_config_{image,text}_prod.json` |
| Early test config | `tap-art-grader-test` = `0a763d98-…` (v3 has the submission_id echo) |
