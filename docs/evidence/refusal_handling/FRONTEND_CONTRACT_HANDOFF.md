# API change: how "out of credits" and "not on your plan" are answered

For: whoever maintains the GradeA+ frontend.
Backend change: H-24, branch `task/refusal-handling`. Accepted by the product owner
on 2026-09-18, to land on QA (beta) first.

## What changed, in one paragraph

When the backend refuses an AI request because the user can't pay for it, or their
plan doesn't include the feature, it used to answer inconsistently: sometimes
**400** with HTML markup in the message, sometimes **500** as if the server had
crashed, and sometimes with internal billing numbers in the text. Every one of those
refusals now answers the same way: **402** or **403**, a machine-readable **code**,
and a **plain-text** message that's safe to show as-is.

## What to do in the frontend

1. **Branch on the code, not on the status or the message text.** There are two:

   | `code` | Status | Meaning | Suggested UI |
   |---|---|---|---|
   | `insufficient_credits` | 402 | The credit wallet can't pay for this AI task. | Point teachers to top-up / billing; tell students to ask their teacher. |
   | `ai_feature_not_available` | 403 | The plan or account doesn't allow this AI feature right now. | Point teachers to their plan / subscription; tell students to ask their teacher. |

2. **Read the code from `error.field_errors.code`.** Every response goes through the
   API's standard envelope, so the code is NOT at the top level:

   ```json
   {
     "success": false,
     "message": "<plain text, safe to display>",
     "error": {
       "field_errors": {
         "error": "<same plain text>",
         "code": "insufficient_credits"
       }
     }
   }
   ```

3. **Render `message` as text, never as HTML.** It no longer contains markup. If the
   frontend was injecting it as HTML to make `<b>Insufficient Credits:</b>` bold,
   switch to text rendering; nothing will be lost.

4. **Don't parse the message text.** It's worded for people and may change. The
   old credit messages carried numbers ("Task requires ~22170 credits, but you only
   have 1000"); those are gone on purpose.

5. **A 403 without a `code` is still an ordinary permission denial** (for example,
   a student calling a teacher-only endpoint). Only a 403 **with**
   `code: "ai_feature_not_available"` means the plan or feature.

## Every affected endpoint, before and after

All paths are under `/api/v1/`.

### A. Empty credit wallet on the 18 credit-guarded endpoints

These check the wallet before doing any work.
**Before:** `400`, HTML in the message. **After:** `402`, `insufficient_credits`.

| Method | Path | Who calls it |
|---|---|---|
| PATCH | `assignments/{id}` (only when the body includes `raw_input`) | Teacher |
| PATCH | `assignments/{id}/update-async` | Teacher |
| POST | `assignments/upload-async` | Teacher |
| POST | `assignments/{id}/grade-all` | Teacher |
| POST | `assignments/{id}/schedule_grade_all_submission` | Teacher |
| GET | `course/{id}/student-summary` | Teacher |
| POST | `submissions` | Student |
| POST | `submissions/{assignment_id}/upload` | Student |
| POST | `submissions/{assignment_id}/upload-async` | Student |
| POST | `submissions/{assignment_id}/batch-upload` | Teacher |
| PUT | `submissions/{id}` | Student |
| PATCH | `submissions/{id}` | Student or teacher |
| POST | `submissions/{id}/update-async` | Student or teacher |
| POST | `submissions/{id}/grade` | Teacher |
| POST | `submissions/{id}/grade-async` | Teacher |
| POST | `submissions/{id}/schedule-grade-async` | Teacher |
| GET | `submissions/{id}/teacher_feedback` | Teacher |
| PATCH | `submissions/{id}/update-grade` | Teacher |

### B. Refusals that happen while a submission is being processed

The wallet had some credits, but not enough for this task, or the plan doesn't
allow it. **Before:** `400` with the raw reason. **After:** `402` or `403` with a code.

| Method | Path |
|---|---|
| POST | `submissions/{assignment_id}/upload` |
| PATCH | `submissions/{id}` |
| POST | `submissions/{id}/grade` |

### C. The AI assistant chat panels

**Before:** `500`, as if the server had crashed. **After:** `402` or `403` with a code.

| Method | Path | Screen |
|---|---|---|
| POST | `teacher-admin/dashboard/custom-ai-prompt` | Teacher dashboard assistant |
| POST | `school-admin/dashboard/custom-ai-prompt` | School admin dashboard assistant |
| POST | `super-admin/dashboard/custom-ai-prompt` | Super admin dashboard assistant |
| POST | `analytics/beta/custom-ai-prompt` | Super admin analytics assistant |

When the assistant is switched off platform-wide, these return `403`
`ai_feature_not_available` with "The AI analytics assistant is temporarily
unavailable. Please try again later."

### D. Creating or editing an assignment from text, synchronously

**Before:** `500` with "An unexpected error occurred. Please try again."
**After:** `402` or `403` with a code.

| Method | Path |
|---|---|
| POST | `assignments` (with `raw_input`) |
| PATCH | `assignments/{id}` (with `raw_input`) |

### E. Background tasks you poll: `GET tasks/status/{task_id}`

No status code changes here; the task still ends as a failure. Two things change:

- A refused task **fails once, immediately.** Two task types — answer uploads
  (`submissions/{assignment_id}/upload-async`) and answer edits
  (`submissions/{id}/update-async`) — used to retry a refusal up to 3 times first, so
  you'd see "Retrying (1/3)"-style steps before the failure. Those steps no longer
  appear for refusals. Other task types never retried and are unchanged in this
  respect.
- The **error text** changed:
  - Out of credits: always "There aren't enough AI credits available for this. The
    credit wallet needs to be topped up before it can run." (It used to include
    balances and estimates.)
  - A **student** blocked by their teacher's plan: always "AI features aren't
    available for this assignment right now. Please ask your teacher to check their
    account." (It used to describe the teacher's subscription, e.g. "Trial period
    has expired" — a privacy problem, now fixed.)

### Known gap: `POST assignments/generate/{course_id}`

This endpoint already answered `402` / `403` before this change, so its status codes
are unchanged. Its out-of-credits message is now the generic one, like everywhere
else. But **it does not include a `code` yet.** Until a backend follow-up adds one,
treat `402` from this endpoint as `insufficient_credits` and `403` from it as
`ai_feature_not_available`.

## Real response bodies

All of these were captured from the running code, before and after the change.

**Empty wallet, teacher, `POST assignments/{id}/grade-all`**

Before (`400`):
```json
{"success":false,"message":"<b>Insufficient Credits:</b> Your Credit Wallet is currently empty. Please top up your credits to continue with grading or AI tasks.","error":{"field_errors":{"detail":"<b>Insufficient Credits:</b> Your Credit Wallet is currently empty. Please top up your credits to continue with grading or AI tasks."}}}
```
After (`402`):
```json
{"success":false,"message":"There aren't enough AI credits available for this. The credit wallet needs to be topped up before it can run.","error":{"field_errors":{"error":"There aren't enough AI credits available for this. The credit wallet needs to be topped up before it can run.","code":"insufficient_credits"}}}
```

**Not enough credits for the task, teacher assistant chat**

Before (`500`):
```json
{"success":false,"message":"Task requires ~22170 credits, but you only have 1000 credits. Please refill your wallet to continue","error":{"field_errors":{"error":"Task requires ~22170 credits, but you only have 1000 credits. Please refill your wallet to continue"}}}
```
After (`402`): identical to the "empty wallet, after" body above.

**Not on the plan, teacher assistant chat**

Before (`500`):
```json
{"success":false,"message":"AI access denied: No active subscription","error":{"field_errors":{"error":"AI access denied: No active subscription"}}}
```
After (`403`):
```json
{"success":false,"message":"AI access denied: No active subscription","error":{"field_errors":{"error":"AI access denied: No active subscription","code":"ai_feature_not_available"}}}
```
A teacher still sees the real reason about their own account.

**A student blocked by their teacher's plan, `PATCH submissions/{id}`**

After (`403`):
```json
{"success":false,"message":"AI features aren't available for this assignment right now. Please ask your teacher to check their account.","error":{"field_errors":{"error":"AI features aren't available for this assignment right now. Please ask your teacher to check their account.","code":"ai_feature_not_available"}}}
```
Before, the student received the teacher's billing reason instead.

## Quick checklist for the frontend

- [ ] Nothing renders the error `message` as HTML.
- [ ] Nothing branches on `status === 400` to detect "out of credits".
- [ ] Nothing treats a `500` from the AI assistant panels as a credits or plan problem.
- [ ] Code is read from `error.field_errors.code`.
- [ ] 402 / `insufficient_credits` and 403 / `ai_feature_not_available` each show a
      message suited to the role: teachers get directed to billing or their plan,
      students get told to ask their teacher.
- [ ] A 403 without a `code` still means an ordinary permission denial.
- [ ] Polled background tasks don't expect "Retrying" steps before a refusal fails.
