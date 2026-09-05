# Kaapi ASSESSMENT — setup & test runbook (reproduce it yourself)

**Branch:** `feat/kaapi-llm-eval` · **Date:** 2026-09-05
A linear, copy-pasteable guide to stand up the Kaapi ASSESSMENT pipeline **locally** and verify
it **end to end**. This is the reproducible version of the work in this session. For concepts see
`KAAPI_DEVELOPER_GUIDE.md`; for behavior see `KAAPI_USER_FLOW.md`; for production see
`KAAPI_DEPLOY_CHECKLIST.md`.

> You need: Docker, a Kaapi API key (with the ASSESSMENT feature enabled), and — for private
> images — a GCS service-account JSON scoped to read the submissions bucket.

---

## Part 1 — Local Frappe bench (Docker)

The repo `rag-bench/` has a `docker-compose.yml` (mariadb, redis, rabbitmq, a mock LMS, and a
`frappe/bench` container with the app source mounted).

```bash
cd rag-bench
docker compose up -d
docker compose ps            # all services Up
```

First-time bench/site creation (skip if the `frappe-bench` volume already has a site):
```bash
docker exec -u frappe rag-bench-frappe-1 bash -lc '
  cd /home/frappe && bench init --skip-redis-config-generation --python python3.12 frappe-bench &&
  cd frappe-bench &&
  bench new-site rag.localhost --db-root-password root123 --admin-password admin --no-mariadb-socket &&
  bench --site rag.localhost install-app rag_service'
```

Install runtime deps into the bench env (once):
```bash
docker exec -u frappe rag-bench-frappe-1 bash -lc '/home/frappe/frappe-bench/env/bin/pip install \
  langchain-openai pika together google-cloud-aiplatform google-cloud-storage opencv-python-headless'
```

Apply the doctype changes (adds the `kaapi_*` fields):
```bash
docker exec -u frappe rag-bench-frappe-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site rag.localhost migrate'
```

> When editing app code on the host, sync it into the bench app dir if the app is a copy rather
> than a symlink to the mount: `cp <mount>/rag_service/... /home/frappe/frappe-bench/apps/rag_service/rag_service/...`

Seed an LLM Settings row for Kaapi (holds the API key). In the Frappe UI (`/app`) or via
`bench execute`, create an active `LLM Settings` with `provider=Kaapi`, `model_name=gpt-4o`,
and the API key in `api_secret`.

---

## Part 2 — Kaapi side (one-time, via curl or Postman)

Base URL `https://api.kaapi.ai`, auth header **`X-API-KEY: ApiKey <key>`**.

### 2a. Register the google-gcp credential (for private `gs://` images)
```bash
curl -sX POST https://api.kaapi.ai/api/v1/credentials \
 -H "X-API-KEY: ApiKey $KAAPI_KEY" -H "Content-Type: application/json" \
 -d '{"provider":"google-gcp","name":"tap-gcs-readonly","credentials":{
      "api_key":"<GCP_API_KEY>","project_id":"<PROJECT_ID>","location":"us-central1",
      "gcs_bucket":"assignment_submission_tap","sa_key":<PASTE_SA_JSON_OBJECT>}}'
```
> The SA must have only `roles/storage.objectViewer` on that bucket (read-only).

### 2b. Create the two configs
Bodies are in `Desktop/RRM/kaapi_config_image_prod.json` and `kaapi_config_text_prod.json`.
```bash
curl -sX POST https://api.kaapi.ai/api/v1/configs \
 -H "X-API-KEY: ApiKey $KAAPI_KEY" -H "Content-Type: application/json" \
 --data-binary @kaapi_config_image_prod.json    # -> returns config_id (image)

curl -sX POST https://api.kaapi.ai/api/v1/configs \
 -H "X-API-KEY: ApiKey $KAAPI_KEY" -H "Content-Type: application/json" \
 --data-binary @kaapi_config_text_prod.json     # -> returns config_id (text)
```
Note the returned `config_id`s (session values: image `67865d5f-…`, text `ec7cb5ec-…`, both v1).

> The config's `json_output_schema` must mirror the prompt template's `response_format` + an
> echoed `submission_id`, in OpenAI strict form (every property in `required`,
> `additionalProperties:false`). Validate a config with one `POST /assessments` before wiring.

---

## Part 3 — Environment

Set these where the bench processes can see them (the submit job and the web server):
```bash
export RAG_EVAL_MODE=assessment
export KAAPI_API_KEY=<key>                 # or rely on LLM Settings
export KAAPI_BASE_URL=https://api.kaapi.ai
export KAAPI_IMAGE_CONFIG_ID=67865d5f-bc53-4708-9858-eb766132c0db
export KAAPI_IMAGE_CONFIG_VERSION=1
export KAAPI_TEXT_CONFIG_ID=ec7cb5ec-6403-4c8c-98e9-ed6c963d506f
export KAAPI_TEXT_CONFIG_VERSION=1
export KAAPI_WEBHOOK_SECRET=<secret>
export KAAPI_CALLBACK_URL='https://<public-host>/api/method/rag_service.api.kaapi_webhook.receive?token=<secret>'
```

For a local test with no public host, point `KAAPI_CALLBACK_URL` at a webhook.site inbox (to
capture Kaapi's payload) OR use a `cloudflared`/`ngrok` tunnel to your bench (Part 5b).

---

## Part 4 — Seed test submissions

Give the bench some `Pending` rows. `submissions.csv` (in `Desktop/RRM`) has real image URLs +
levels. Extract N rows on the host into the mounted app dir, then seed them:

```python
# host: write rows the bench can read (mounted at /workspace/rag_service)
import csv, json
rows=[]; seen=set()
for r in csv.DictReader(open('submissions.csv')):
    u=(r.get('gcs_url') or '').strip()
    if u and u not in seen:
        seen.add(u); rows.append({'level':(r.get('level') or 'Level 1').strip(),'url':u})
    if len(rows)>=12: break
open('<mount>/rag_service/_seed.json','w').write(json.dumps(rows))
```
```python
# bench: bench --site rag.localhost execute path.to.this  (or run via the env python)
import json, frappe
frappe.init(site="rag.localhost"); frappe.connect()
for i,r in enumerate(json.load(open("/workspace/rag_service/_seed.json"))):
    d=frappe.new_doc("Feedback Request"); d.update({
      "submission_id":f"E2E-{i:02d}","student_id":f"S{i}","assignment_id":"POP-ART-1",
      "submission_type":"image","submission_url":r["url"],"grade":"5","level":r["level"],
      "language":"English","status":"Pending","is_plagiarized":0,"is_ai_generated":0})
    d.insert()
frappe.db.commit()
```
> `assignment_id=POP-ART-1` makes the mock LMS return a canned Arts rubric, so no real LMS is
> needed locally.

---

## Part 5 — Run the pipeline

### 5a. Phase 1 — submit
```bash
docker exec -u frappe -e RAG_EVAL_MODE=assessment \
  -e KAAPI_IMAGE_CONFIG_ID=67865d5f-bc53-4708-9858-eb766132c0db -e KAAPI_IMAGE_CONFIG_VERSION=1 \
  -e KAAPI_CALLBACK_URL='https://webhook.site/<your-uuid>' \
  rag-bench-frappe-1 bash -lc \
  'cd /home/frappe/frappe-bench && bench --site rag.localhost execute rag_service.core.batch_runner.run_submit_phase'
```
Rows go `status=Processing`, `kaapi_status=Submitted`, and an `assessment_id` is stored. This
really calls Kaapi (spends grading credits).

### 5b. Phase 2 — receive the results
Two ways:

**(i) Capture via webhook.site (no public host).** Kaapi POSTs to your webhook.site inbox; fetch
it via its API and replay through the real handler:
```python
# host: pull the COMPLETED payload for your assessment_id
import json, urllib.request
AID="<assessment_id>"
data=json.loads(urllib.request.urlopen(
  "https://webhook.site/token/<your-uuid>/requests?sorting=newest&per_page=25").read())["data"]
payload=next(r["content"] for r in data if AID in (r.get("content") or "") and '"status":"COMPLETED"' in r["content"])
open("<mount>/rag_service/_wh.json","w").write(payload)
```
```python
# bench: replay through the SAME logic the endpoint uses
import json, frappe; frappe.init(site="rag.localhost"); frappe.connect()
from rag_service.api.kaapi_webhook import process_payload
print(process_payload(json.load(open("/workspace/rag_service/_wh.json"))))
```

**(ii) Real HTTP endpoint (proves `receive()` over HTTP).** Start the web server and POST the
captured payload to the live endpoint:
```bash
docker exec -d -e KAAPI_WEBHOOK_SECRET=e2e-secret rag-bench-frappe-1 \
  bash -lc 'cd /home/frappe/frappe-bench && bench serve --port 8000'
# wrong token -> 401 ; correct token -> {ok, completed:N}
curl -s -H "Host: rag.localhost" -H "Content-Type: application/json" \
  --data-binary @_wh.json \
  'http://localhost:8000/api/method/rag_service.api.kaapi_webhook.receive?token=e2e-secret'
```

**(iii) True end-to-end (Kaapi calls you).** Run a tunnel so Kaapi reaches your bench directly:
```bash
cloudflared tunnel --url http://localhost:8000      # gives https://<random>.trycloudflare.com
# set KAAPI_CALLBACK_URL=https://<random>.trycloudflare.com/api/method/rag_service.api.kaapi_webhook.receive?token=<secret>
# re-run 5a; rows flip to Completed on their own when Kaapi calls back.
```

---

## Part 6 — Verify
```python
# bench
import json, frappe; frappe.init(site="rag.localhost"); frappe.connect(); frappe.db.rollback()
rows=frappe.get_all("Feedback Request", filters={"submission_id":["like","E2E-%"]},
                    fields=["submission_id","status","kaapi_status","generated_feedback"])
print("Completed:", sum(1 for r in rows if r.status=="Completed"), "of", len(rows))
for r in rows[:3]:
    fb=json.loads(r.generated_feedback or "{}")
    print(r.submission_id, r.status, fb.get("final_grade"),
          {e.get("Skill"):e.get("grade_value") for e in fb.get("rubric_evaluations",[])})
```
Expect all rows `Completed`, each with `final_grade`, `rubric_evaluations`, `overall_feedback`,
and delivery to the `feedback_results` queue (see the consumer log "Feedback sent successfully").

Failure/idempotency checks to try:
- Submit a batch with one **bad image URL** → that row ends `Failed`, others deliver.
- **Replay** the same webhook payload → second run reports `skipped` (no double-delivery).

---

## Part 7 — Flip modes / cleanup
- Enable the flow: `RAG_EVAL_MODE=assessment`. Roll back: unset it (or `=llm_call`).
- Nightly cron (`hooks.py`, 01:00) runs `run_nightly_batch`, which dispatches to `run_submit_phase`
  in assessment mode.
- Stop the bench: `cd rag-bench && docker compose down` (add `-v` to wipe volumes).

---

## What this proves
Running Parts 1–6 exercises every real component: day-time deferral → prompt rendering → real
`/assessments` submit → real Kaapi grading of real images → real webhook payload → secret check +
body parse (5b-ii) → id-based correlation → `finalize_feedback` → LMS delivery. The only piece
that requires infra (not code) is Kaapi literally reaching your endpoint over the internet
(5b-iii / production public URL).
