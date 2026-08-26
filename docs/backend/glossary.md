# Glossary

> Part of the [backend reference](README.md).

Terms in this codebase that mean something more specific than they sound like.

---

## Domain

**Assignment** — a set of questions a teacher gives to a course. Created by typing, uploading a scan, or asking the AI to generate one; in every case it passes through AI extraction into structured `questions` JSON. → [assignments.md](assignments.md)

**Submission** — one student's answers to one assignment. **Exactly one row per `(student, assignment)`** — a resubmission overwrites rather than appending. → [students-and-submissions.md](students-and-submissions.md)

**Session** *(this codebase)* — an **academic period** (a term or semester), not an HTTP session and not a login. Owned by either one teacher or a school. → [classrooms.md](classrooms.md)

**Course** — what a teacher teaches inside a session. Called a "section" in some older code and constraint names.

**Enrolment** (`StudentCourse`) — the link between a student and a course. Carries `final_grade`, a derived value.

**Proxy upload** — a teacher uploading a student's paper on their behalf, where the system works out whose paper it is from the name written on it. The looser of the two upload paths, and the one that can misattribute silently.

**Rigor** — a 0–5 score for how much real thinking an assignment demands, blended from three components. Deliberately paired with a plain-English verdict, because *"a teacher who sets harder questions but marks generously can score below one who sets easier questions marked honestly."* → [assignments.md](assignments.md#rigor-scoring), [dashboard.md](dashboard.md#rigor-roll-up)

**Demand / Evidence / Standards** — rigor's three components. **Demand** is the level of thinking asked for (from `blooms_level`). **Evidence** is whether anyone was actually stretched, inverted from achieved scores — **so a *higher* evidence number means students did *worse***. **Standards** is whether open-ended questions carry a usable rubric.

**At-risk** — one shared definition of "this student needs attention", so every screen and email agrees: `critical_grade OR critical_missing_work OR (2 of 3 moderate flags)`. → [dashboard.md](dashboard.md#at-risk-classification)

---

## Grading

**Grader A / Grader B** — the first model to mark a question, and the different model that independently re-marks a selected subset **blind**. **Grader A's score always stands**; B can only flag. → [ai-processor.md](ai-processor.md#second-opinion)

**Second opinion** — that blind re-marking. Triggered selectively, never as a blanket second pass.

**Blind** — grader B's prompt contains only the question and the answer. It cannot see grader A's score.

**Rubric level / ladder** — the discrete point values a rubric defines. Scores are **snapped** to the nearest one, and accuracy is measured in *level-index space* rather than raw points, because *"5 points is a whole grade band on a 10-point question and a rounding error on a 25-point essay."*

**Snapping** — forcing a model's score onto the nearest rubric level. Ties resolve **downward** — *"never inflate a grade on a coin-flip."* 0 is always a candidate.

**Evidence quotes** — verbatim spans from the student's answer that a points-awarding evaluation must cite, **string-matched in Python**. *"A grader forced to cite evidence whose citations are string-matched cannot invent justifications — it is the cheapest possible 'second opinion', costing zero extra model calls."* → [ai-processor.md](ai-processor.md#evidence-verification)

**Desugaring** — folding LaTeX cosmetics (`$H_2$` → "H2") before matching a quote, because the model *"routinely re-typesets its own quote while composing it."* Calibrated against 21 real submissions to separate reformatting from invention.

**Tier 0 / Tier 0.5** — deterministic objective grading, then the cross-student answer cache. Both run **before** any LLM call. Tier 0 is **claim-only**: an ambiguous question is deferred, never zeroed.

**`level_decision`** — the model's own per-question "was this a close call?" (`borderline` or `clear`). The per-question uncertainty signal that submission-level confidence turned out not to be: *"a live benchmark run had 120 of 124 questions at confidence >= 80, so there was no spread to select on."*

**Severity tier** — how bad one disagreement is: `critical`, `moderate`, `borderline`. **Grades a disagreement for triage; never decides whether one exists.**

**Review queue** — submissions flagged `needs_review`, sorted by a **tier-weighted** key so a critical disagreement always outranks a moderate one regardless of the raw point gap.

**Answer status** — `ANSWERED`, `BLANK`, `ILLEGIBLE`, `NOT_FOUND_IN_DOCUMENT`. The last means *"we may not have the student's work"* and is always critical.

**Blank verification** — re-reading only the questions extraction called empty. The one transition it can make is `BLANK → NOT_FOUND_IN_DOCUMENT`; **it never writes a transcription.**

---

## Billing

**Credit** — the unit of AI consumption, charged 1:1 against provider tokens. **Stored raw = display × 1000.** A "5,000 credit" plan holds `5_000_000`.

**Wallet** — the container. Holds no balance of its own; every figure is the sum of its live buckets.

**Bucket** — one pool of credits. `MONTHLY`, `CARRY_OVER`, `OVERAGE`, `MANUAL_GRANT`, `TRIAL`. Drained in **that order**, because *"CARRY_OVER and TRIAL are one-shot pools that are permanently forfeited … whereas unused MONTHLY balance gets another chance."*

**Rollover / carry-over** — unused monthly credits moved into a `CARRY_OVER` bucket at renewal, subject to `carry_over_percent` and `max_bank`.

**`max_bank`** — a ceiling on total live banked balance. **The monthly grant is never trimmed**; only the carryover portion is reduced to make room.

**Overage block** — a purchasable chunk of extra credits beyond the plan, priced per block.

**Individual track / licence track** — the two ways of paying. Personal email → individual. Business email → school licence. **No merge path exists.** → [users-and-auth.md](users-and-auth.md#the-personal-vs-business-email-fork)

**Allocation** (`SchoolCreditAllocation`) — one teacher's seat on a licence. Each has its **own** wallet, not a shared pool.

**Admin allocation** — the school admin's own fixed 5,000-credit analytics grant. Flagged `is_admin_allocation` and excluded from every seat count and consumption roll-up.

**Offline billing** — a licence invoiced and paid by transfer, recorded by a superadmin in `LicenseBillingRecord`. No Stripe involved.

**Refund scope** — a context manager that reclaims every credit charge made inside it if the block raises. Replaces wrapping a multi-call pipeline in one transaction, *"without holding any lock or transaction open across network I/O."* → [billing-core.md](billing-core.md#billing_refund_scope--the-multi-call-wrapper)

**Deferred change** — a plan change that takes effect at the cycle end rather than immediately. Downgrades always; upgrades only when crossing annual → monthly, because *"Stripe's interval-crossing proration produces an unrefunded credit balance rather than a clean charge."*

---

## Infrastructure

**Claim** — a database row checked out before doing expensive work, so a duplicate delivery backs off. Implemented as a **single conditional UPDATE whose row count is the result**. Two exist: the grading claim and the Stripe event claim. → [async-and-infrastructure.md](async-and-infrastructure.md#idempotency)

**Stale claim** — a claim older than a window **derived from a hard kill point**, so the holder is provably dead rather than merely slow. A *tight* window is the dangerous direction.

**Fencing token** — `claimed_at`, used to prove a late writer still owns the row. Without it *"a slow-but-failing original could flip a freshly SUCCEEDED row back to FAILED."*

**Visibility timeout** — how long Redis waits for an ack before redelivering a task. Raised to 3600s after a 600s value **double-billed a teacher**.

**Single-flight** — collapsing concurrent requests for the same PDF into one render. **Per-process on purpose** — a distributed lock's failure modes *"are far worse than the duplicate work it would save."*

**Load shedding** — refusing a render outright rather than queueing it. Measured: without it, *"renders sat ~35s and 89 of 3000 eventually died at the 45s bound, each having held a request thread the whole time."*

**Warm browser** — the long-lived Chromium instance per process, recycled after N renders — **but only at a quiet moment**, because waiting for in-flight renders under the swap lock *"dragged nine ~0.2s renders out to ~5s each."*

**Content-addressed cache** — a cache whose key contains everything that determines the value (`updated_at` for PDFs, a content hash for grades), so **nothing needs manual invalidation**.

**Correlation ID** — one `X-Request-ID` spanning a request, its logs, its Sentry event, and every task it dispatches.

**`safe_delay` vs `launch_processing_task`** — the silent and loud ways to dispatch a task. Silent for side effects; loud (503) for work a user is waiting on.

**Tracked task** (`BackgroundProcessingTask`) — the progress row every long-running job reports into. Its `error` column holds a **user-facing sentence**, never a traceback.

**Expand-contract** — the three-deploy pattern for a non-additive migration. CI refuses one without an explicit `# expand-contract-step:` acknowledgement.

---

## QA and quality

**Replay** — running the grading benchmark against **recorded** model responses. Free and deterministic; catches regressions in *our* code and **by construction cannot** notice the model changing.

**Live** — the same benchmark against the real model. Costs credits; the **only** thing that can detect the provider changing behaviour.

**Baseline** — the committed metrics the nightly replay diffs against.

**Run history** — three tiers: JSONL in git (headline + per-question), and a full raw archive on Cloudinary, kept *"so it can answer questions we have NOT thought of yet."*

**Live QA** *(billing)* — driving **real** Stripe test-mode objects through real billing code. *"Every other test in this codebase mocks Stripe, which means they encode our BELIEFS about Stripe's API rather than its behaviour."*

**The two-clock problem** — a Stripe test clock moves Stripe's time; `timezone.now()` does not move at all. Advancing only one makes every local "is this due?" guard correctly say no, so **nothing happens and it looks like a bug**.

**Test clock** — Stripe's simulated time. **Can only be attached when a customer is created**, which is why `BILLING_TEST_CLOCK_EMAIL_DOMAINS` exists.

**Invariant** — a property that must hold after **every** step of every scenario. *"A scenario asserts what its author thought to check … An invariant catches bugs in sequences nobody scripted."* An invariant that itself throws is `ERROR`, never `VIOLATED`.

**Chaos walk** — a seeded random sequence of real actions, plus a shrinker. Finds bugs that only appear from a specific **interleaving**, *"exactly the class this suite's fixed scenarios cannot find by construction."*

**Unreachable** *(benchmark verdict)* — a score that is not a rubric level value. Indicates a **snapping regression in our own pipeline**, not a model error.

---

## Codebase idioms

**"Fails closed"** — an unrecognised or missing value is refused rather than assumed safe. Used for email classification, `DEBUG`, enforcement modes, and the live-QA key check.

**"Claim-only"** — a component that may take work but never rejects it. Tier 0 objective grading is claim-only: *"Adding this tier can therefore only remove error relative to the status quo."*

**"Degrades to log"** — a strict check that becomes advisory on the **final** attempt, because *"failing on the final attempt destroys the whole submission, and the student gets no grade at all."*

**"Never downgrade what we can't measure"** — a missing signal sorts mid-band, not last. Applied to `gap_fraction`, severity tiers, and review ordering.

**"The single arithmetic authority"** — `_finalize_grading_result`. Model-reported totals are never used.

**"Read-only by design"** — the two audit commands. *"The repairs are business decisions … not things to do behind anyone's back."*

**"Human-gated"** — `replay_stripe_events`, `--dry-run` by default, because a handler that failed after issuing a refund would issue it twice.

**System-generated email** — an `@student.local` placeholder for a student with no real address. Never a mailbox; exempt from the email fork; returned as `null` by the API; excluded from every outbound mail queryset.

---

## Abbreviations

| | |
|---|---|
| **OTP** | one-time password — a 6-digit numeric code |
| **SSRF** | server-side request forgery — the threat `fetch_url_content` guards against |
| **TOCTOU** | time-of-check to time-of-use — the race `select_for_update` closes on the submission limit |
| **KaTeX** | the maths typesetting library, **vendored locally** so a render never needs the network |
| **ProseMirror** | the rich-text document format `raw_input` holds — **as serialised JSON text**, not a dict |
| **Bloom's level** | the cognitive-demand taxonomy: remember, understand, apply, analyze, evaluate, create |

---

## Things that are not what they sound like

| Term | Actually |
|---|---|
| `grading` app | **an empty stub.** Grading lives in `ai_processor` + `students` |
| `ocr_processor` app | **an empty stub.** OCR is the vision model's job |
| `Session` | an academic term |
| `ai_generated` | **inverted** — extraction sets it to `False` |
| `BetaWhitelist` / `Waitlist` | gate nothing; kept as records |
| `CourseCategory` | an orphan table |
| `Assignment.teacher` | marked *"IN REVIEW FOR REMOVAL"* — use `course.teacher` |
| `is_new_student` | means "not yet activated" |
| `processed_at` on `StripeEvent` | when **first seen**, not completion |
| `attempt_count` | only counts **student self-uploads** |
| `evidence` (rigor) | runs **opposite** to student scores |
| `PDFService` | **two different classes** with this name — the one in `assignments/services.py` is unused |
