# Kaapi ASSESSMENT — end-to-end user flow

**Branch:** `feat/kaapi-llm-eval` · **Date:** 2026-09-05
Companion to `KAAPI_ASSESSMENT_SWITCH_SCOPE.md` and `KAAPI_DEPLOY_CHECKLIST.md`.
Describes the full lifecycle of a submission under the Kaapi ASSESSMENT (batch + webhook) flow.

## Actors
| Actor | Role |
|---|---|
| **Student / LMS** | Where a submission originates and where feedback is shown |
| **Plagiarism service** | Existing upstream plagiarism/AI check |
| **rag_service** | Our app — the grading pipeline |
| **Kaapi** | Project Tech4Dev's AI grading platform |
| **RabbitMQ** | `plagiarism_results` (inbound to us), `feedback_results` (outbound to LMS) |

## Key idea
Image/text submissions are **not graded in real time** anymore. During the day they are parked
as `Pending`. A **nightly batch** hands them all to Kaapi at once; Kaapi grades them and **calls
our webhook** with the results, which finishes each one and delivers it to the LMS. Audio/video
are still graded in real time.

---

## Phase A — Daytime: a student submits

1. **Student submits** an image/text assignment in the LMS.
2. **Plagiarism/AI check** runs upstream; the result + submission is published to the
   **`plagiarism_results`** queue.
3. **rag_service consumer** picks it up (`FeedbackHandler.handle_submission`):
   - **Audio / video** → graded **immediately** in real time (unchanged).
   - **Image / text / emoji** → **deferred**: a `Feedback Request` is saved with
     `status = Pending`, then we stop. No grading yet.

> Result: image/text submissions accumulate as `Pending` through the day.

## Phase B — Night (≈1 AM): hand off to Kaapi

4. **Cron fires** → `enqueue_nightly_batch` → `run_submit_phase`.
5. **Gather all `Pending` image/text rows.** For each:
   - **Flagged** (plagiarised/AI) → generate canned feedback and **deliver immediately** to the
     LMS queue (no grading). Done.
   - **Clean** → render the full grading prompt (rubric + description + level + language baked
     in) — identical to the real-time prompt.
6. **Submit to Kaapi** — one `/assessments` batch per media type, each row carrying its
   `submission_id`, `rendered_prompt` (+ image URL), and our `callback_url` (webhook + secret).
7. **Mark rows `Submitted`**, store the `assessment_id`, and the 1 AM job **finishes quickly** —
   it has handed off the work, it does not wait.

## Phase C — Kaapi grades (minutes–hours later)

8. **Kaapi grades the whole batch in parallel**, reading images directly from the private bucket
   via the registered google-gcp credential.

## Phase D — Completion: Kaapi calls our webhook

9. **Kaapi POSTs the results** to our `callback_url`:
   `POST /api/method/rag_service.api.kaapi_webhook.receive?token=<secret>`.
10. **Our webhook (`receive`)**:
    - verifies the `token` (else 401),
    - parses the batch body,
    - **matches each result to its submission by the echoed `submission_id`**,
    - graded row → `finalize_feedback` → `process_feedback` → **push to `feedback_results`**,
    - un-graded row (null assessment) → marked `Failed` (retried next batch).

## Phase E — Back to the student

11. **LMS consumes** from `feedback_results` and shows each student their feedback (grade +
    observations + translated feedback), same format as always.
12. **Safety net — stuck-row sweeper:** a scheduled job re-submits any row stuck in `Submitted`
    too long (a lost webhook), since Kaapi results cannot be polled.

---

## Sequence diagram

```
STUDENT/LMS      PLAGIARISM        rag_service            KAAPI            LMS (feedback)
    |                |                  |                    |                   |
 1  |-- submit ----->|                  |                    |                   |
 2  |                |-- plag result -->| (plagiarism_results queue)             |
 3  |                |                  |  image/text? -> save Pending, STOP     |
    |                |                  |  audio/video? -> grade now (unchanged) |
    .                .                  .                    .                   .
    .            (through the day, image/text pile up as Pending)               .
    .                .                  .                    .                   .
 4  |                |            [1 AM cron]                |                   |
 5  |                |                  |  render prompts     |                   |
    |                |                  |  flagged -> deliver canned ------------>|
 6  |                |                  |-- POST /assessments (batch + callback)->|
 7  |                |                  |  mark Submitted; job ends               |
    .                .                  .                    | grades in parallel .
 9  |                |                  |<-- POST results (webhook + token) ------|
10  |                |                  |  verify token, match by submission_id   |
    |                |                  |  finalize -> deliver ------------------>|
11  |<-- feedback shown ----------------------------------------------------------|
```

## Real-time vs deferred — what the student experiences
| Submission type | Behavior | Feedback timing |
|---|---|---|
| Audio / video | Graded in real time (unchanged) | Within minutes |
| Image / text / emoji | Deferred → nightly batch → Kaapi → webhook | **Next morning** |

The one visible change: image/text feedback arrives **overnight** instead of within minutes — a
deliberate trade for the batch + credential architecture.

## Failure & edge handling
- **Flagged (plagiarism/AI):** canned feedback delivered inline during submit; never sent to Kaapi.
- **A row fails grading** (Kaapi returns `assessment: null`): marked `Failed`, not delivered,
  re-picked in the next night's batch. Other rows in the batch still deliver.
- **Duplicate/replayed webhook:** idempotent — only `Submitted` rows are processed, so already
  `Completed` rows are skipped.
- **Lost webhook:** the stuck-row sweeper re-submits rows left in `Submitted` past N hours.
- **Correlation:** by echoed `submission_id` (order-independent); a mismatched/hallucinated id
  fails safe (row re-graded) rather than mis-assigning a grade.

## Rollback
Set `RAG_EVAL_MODE=llm_call` (or unset) → the nightly batch reverts to the synchronous
`/llm/call` grade-and-deliver path. No code change, no redeploy of logic.

## Configuration touchpoints
- `RAG_EVAL_MODE=assessment` — enables this flow
- `KAAPI_IMAGE_CONFIG_ID` / `KAAPI_TEXT_CONFIG_ID` (+ `_VERSION`) — the Kaapi configs
- `KAAPI_CALLBACK_URL` — public webhook URL incl. `?token=<secret>`
- `KAAPI_WEBHOOK_SECRET` — must equal the token in the callback URL
- Nightly cron time — `hooks.py` `scheduler_events` (default 01:00)
