# 07 — Epic I-1 Implementation Plan (BE-I-04 provenance + `SubmissionGrading` expand step)

**Status:** plan for review. Nothing here is implemented.
**Companion to:** [05_epics_b_to_i_roadmap.md](05_epics_b_to_i_roadmap.md) (§2.2 option (a), §3.8, §7),
[01a_requirements_specification.md](01a_requirements_specification.md) (FR-I-04, FR-I-05, NFR-MDL-03, NFR-OBS-04, NFR-TST-01),
[03a_data_model.md](03a_data_model.md) (§2.12 `SubmissionGrading`, §2.13, §4.4),
[03_architecture.md](03_architecture.md) (X-1),
[06_epic_b0_implementation_plan.md](06_epic_b0_implementation_plan.md) (style, gate table).
**Size:** **M, about 2 weeks** for one engineer. The roadmap said "BE-I-04 ~1 wk"; §0 explains why that was too low.
**Code facts are from beta at 4b902fc.** Facts about `audit/` are from `task/epic-a-land`. Anything not confirmed is marked **[UNVERIFIED]** and listed in §10.
Line numbers come from a full read of the write path plus a survey of every
writer and reader of the grade fields (2026-09-26). Re-check them at build time.

**What I-1 is.** Stage 1 of the three-deploy expand → migrate → contract plan
in 03a §2.12: create `SubmissionGrading`, back-fill it, **dual-write** it from the
grading pipeline, and stamp prompt / config / strictness / model on every new
run (BE-I-04). **What it is not:** no reader changes, no contract step, no
strictness scale, no re-grade at a different strictness. Those are I-2.

---

## 0. Corrections to the prior documents

### 0.1 03a §2.12 describes a simpler `StudentSubmission` than exists

03a says `StudentSubmission` holds `score`, `score_percentage`, `max_points`,
`feedback`, `graded_at` inline. On beta (`students/models.py`) the grade state is
larger:

| Group | Columns | Notes |
|---|---|---|
| Final grade | `score`, `score_percentage`, `max_points`, `feedback`, `graded_at` | `score` is **Decimal(6,2)**, not (8,2) as in 03a |
| AI result | `ai_score`, `ai_graded_at`, `ai_grading_completed_at`, `ai_feedback` | `ai_score` is written together with `score` by the pipeline. **`ai_feedback` is never written by any code** (survey); it is a dead column |
| Human override | `was_regraded`, `regraded_at` | Set by `update_grade` when a teacher edits the score. `ai_score` stays untouched, so `ai_score` vs `score` is the delta |
| Confidence | `grading_confidence` | **IntegerField 0–100** (`_coerce_confidence`, `students/services.py:264`), not the Decimal(5,4) 03a proposes |
| Review queue | `needs_review`, `review_reasons`, `review_severity`, `review_tier` | Reset on **every** grading run (`students/services.py:416–424`) |
| Idempotency claim | `grading_state`, `grading_started_at` | Claimed by `_claim_submission_for_grading` (`:144`) |
| Other | `formatted_grade`, `is_published`, `scheduled_grading_at`, `attempt_count` | Not run properties |

**Consequence:** the 03a table would drop information or force a second
reconciliation. §3 mirrors what the pipeline actually persists, and calls out where it deviates
from 03a.

### 0.2 "Regrade" already means something else in the code

`was_regraded` / `regraded_at` and the dashboard `regrade_rate`
(`dashboard/views.py:503–505`) all mean "**a human changed the AI's grade**". FR-I-05 "re-grade at a
different strictness" means "**the AI ran again**". Two concepts, one word. In the new table this plan calls the
human edit **`teacher_overridden` / `overridden_at`**, and reserves "regrade" for AI re-runs. The I-2 plan and
the frontend contract need the same vocabulary. **Feature Lead to confirm.**

### 0.3 Provenance is not just unstored: today's answer cache would make it false

`ai_processor/grading_cache.py:94–107`: the Tier 0.5 cache key hashes
`CACHE_VERSION` (a hand-bumped `"v1"`), the **intended** model name, the assignment,
the question and the answer. It does **not** include any prompt or grading-configuration
version. So after a prompt change, cached evaluations produced under the old prompt are
still served, and a run would record the new `prompt_version` for questions that were
never graded with it. This also defeats QA-ACC-12 (the accuracy suite must "run on every
change to a prompt version"): a prompt change would silently hit the cache. The key must
include the versions. In scope, §4.4.

### 0.4 The model actually served is known, but the run's identity is spread over three JSON keys

Confirmed: `_response_model_name` (`ai_processor/services.py:2252`) reads
`response.model`. It ends up in `feedback["grading_model"]`,
`feedback["question_evaluations"][i]["graded_by"]`, and
`feedback["second_opinion"]["model"]`. Nothing is a column. Deterministic-only runs use
the sentinel `"deterministic"` (`ai_processor/objective_grading.py:427`, `services.py:3303`).
**[UNVERIFIED]** whether OpenRouter always fills `response.model`; the code guards for a non-string,
so `grading_model` can be absent. The stamp must have an explicit fallback (`"unknown"`, 03a's word).

### 0.5 The grading configuration is not in one place, and part of it is environment-driven

Configuration today is module constants (`GRADING_QUESTIONS_PER_CHUNK`, `MAIN_MODEL`,
`GRADING_FALLBACK_MODELS`, `ai_processor/services.py:380–397`) **plus** settings
overridable per environment (`GRADING_RESPONSE_SCHEMA_ENABLED`,
`GRADING_EVIDENCE_ENFORCEMENT`, `GRADING_SECOND_OPINION_*`,
`GRADING_DISAGREEMENT_*_FRACTION`, `GRADING_ANSWER_CACHE_*`; `AutoGrader/settings.py:522–721`). X-1 says the
version is "a deploy-time constant"; with env-overridable knobs it is not. §4.2 therefore stamps a
hand-bumped label **and** a hash of the effective values, so an env-only change is
visible in the record.

### 0.6 The 03_architecture reader count for E1 is low (route to E1, not fixed here)

03_architecture §9.8 says `_recalculate_final_grade` plus "three dashboard `Avg()` sites" must move to the
authoritative run. The survey found one weighted-formula site
(`classrooms/signals.py compute_final_grade`, `Sum(score)/Sum(max)`) and **at least eight** aggregation sites reading
`score_percentage` / `score` (`dashboard/views.py:2503–2523, 3037–3042, 3112, 3356–3372, 3491, 3913`;
`dashboard/services.py:1163–1167, 1470–1486`; `dashboard/rigor.py:259–273`), plus ~40 sites reading
`graded_at`. Details in §9. E1 and I-2 need the full list.

---

## 1. Scope

### In scope

1. `SubmissionGrading` model + migration (create-only, additive).
2. A version registry in code for prompt and grading config (`ai_processor/versioning.py`).
3. Stamping prompt / config / strictness / model / fallback on every new grading run.
4. Include prompt and config version in the answer-cache key.
5. Dual-write from the grading pipeline and the two in-place writers (override, mark-reviewed).
6. Idempotent, resumable back-fill command with the legacy sentinel.
7. Parity checker (management command + metric) and a "no un-mirrored writer" test.
8. Tests, mutation matrix, evidence.

### Not in scope (deliberately)

- Moving any reader (dashboard, `compute_final_grade`, serializers) to the new table. **Readers keep reading `StudentSubmission`.**
- Dropping any `StudentSubmission` column (contract step, I-2, its own deploy).
- The strictness scale, `default_strictness`, `Course.strictness`, `Assignment.strictness`, re-grade at another strictness, `superseded_by` (I-2).
- `ai_job` FK (B1 adds it, additive).
- `FeedbackRevision` (E3; it will FK this table).
- Any API field or serializer exposing the new data. **Nothing user-visible changes.**
- Fixing the pre-existing defects in §9 (reported, not fixed).

---

## 2. Build order

1. `ai_processor/versioning.py`: prompt registry, config snapshot, sentinels (§4). Unit tests. No other file changes.
2. Cache-key change (§4.4) with its own tests. Land alone: it invalidates the live cache once on deploy (§4.4).
3. `SubmissionGrading` model + migration (§3).
4. Stamping in the pipeline: `grade_student_submission` returns run provenance (§5.1).
5. Dual-write helper and its three call sites (§5.2–5.3), behind a flag.
6. Back-fill command (§6) and parity command (§7).
7. Gate evidence (§8).

Steps 1–2 are independent of 3–7 and can go first. 3 → 4 → 5 → 6 is a hard sequence.
**Deploy order (three deploys are the rule; I-1 is deploy 1):**

| Deploy | Contents | Rollback |
|---|---|---|
| I-1a | Steps 1–3 (registry, cache key, empty table) | Revert code; drop nothing (table stays, empty) |
| I-1b | Steps 4–5 with the dual-write flag **on** for new runs | Flag off; table keeps whatever it has |
| I-1c | Run back-fill (§6), then parity to zero drift | Delete back-filled rows by sentinel (they are identifiable) |

---

## 3. The model

**App:** `students` · **Table:** `students_submissiongrading` · **Migration class:** additive (new table).
Conventions per 03a §0: UUID PK, `TextChoices`, captured-value identity, no PII.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid4 | PK |
| `submission` | FK → `StudentSubmission` | no | | `on_delete=CASCADE` (as 03a): deleting a student's submission deletes its runs, so per-student deletion (NFR-CMP-04) needs no new code. **[UNVERIFIED]** that every student-deletion path goes through the ORM cascade and not raw SQL |
| `is_authoritative` | bool | no | False | Exactly one True per submission (partial unique index below) |
| `score` | Decimal(6,2) | yes | | Effective score of this run, **including** a later teacher override (mirrors `StudentSubmission.score`) |
| `ai_score` | Decimal(6,2) | yes | | As the AI produced it. Never edited |
| `score_percentage` | Decimal(5,2) | yes | | |
| `max_points` | Integer | yes | | Frozen at grading time (03a rationale) |
| `feedback` | JSON | yes | | The grading dict as produced, **provenance keys excluded** (§5.1). Note the current override path edits `feedback["grading_summary"]` in place (`students/views.py:1045–1048`); I-1 mirrors that on the authoritative row. FeedbackRevision (E3) later moves teacher edits out |
| `graded_at` | datetime | no | now | = `submission.graded_at` |
| `ai_graded_at`, `ai_grading_completed_at` | datetime | yes | | Mirrors |
| `grading_confidence` | Integer | no | 0 | 0–100 as today. **Deviates from 03a Decimal(5,4)** because the source is an int; re-typing is an I-2 decision if a real signal appears (FR-I-08) |
| `needs_review`, `review_reasons`, `review_severity`, `review_tier` | as source | | | Per-run outcome; reset every run today |
| `teacher_overridden` | bool | no | False | ← `was_regraded` (§0.2) |
| `overridden_at` | datetime | yes | | ← `regraded_at` |
| `strictness_level` | char(16) | no | | `STANDARD` for new runs until I-2; `LEGACY` sentinel on back-fill (§0.7 below) |
| `strictness_source` | char(16) | no | | `TEACHER_DEFAULT` until I-2; `LEGACY` on back-fill |
| `prompt_version` | char(64) | no | | §4.1. Back-fill: `legacy-pre-part1` |
| `config_version` | char(64) | no | | §4.2. Back-fill: `legacy-pre-part1` |
| `model_id` | char(128) | no | | Served model, `"deterministic"`, or `"unknown"` |
| `fallback_used` | bool | no | False | Served model ≠ the intended primary (§4.3) |
| `second_opinion_model_id` | char(128) | yes | | From `feedback["second_opinion"]["model"]` |
| `run_meta` | JSON | no | `{}` | Bounded, PII-free: `custom_instructions` (bool), `cached_questions` (int), `models_used` (list), `chunk_count`. Not in 03a; needed for the §0.3 and §4.3 cases |
| `graded_by_id` | UUID | yes | | Captured value: `user.id` passed to `grade_engine` |
| `trace_id` | char(64) | yes | | Correlation id from `request_context.get_request_id()` (04 §0.3a). Char, not UUID: inbound ids are client-shaped strings. **[UNVERIFIED]** format guarantees |
| `created_at` | datetime | no | now | |

**Constraints and indexes**

- `UniqueConstraint(fields=["submission"], condition=Q(is_authoritative=True), name="one_authoritative_grading_per_submission")`. This is the database-level "singular" of QA-I-02, created now so I-2 inherits it.
- `Index(["submission", "-graded_at"])` history.
- `Index(["prompt_version", "config_version", "model_id"])` the QA accuracy suite's attribution query (QA-ACC-12).
- `CheckConstraint(strictness_level in (LENIENT, STANDARD, STRICT, LEGACY))`; likewise `strictness_source`.

**Deliberately omitted vs 03a:** `ai_job` (B1), `superseded_by` (I-2), `second_opinion_model_id` kept. `needs_review` etc.
are in the table because they are per-run outcomes today.

**§0.7 (sentinel decision).** 03a says legacy rows carry a sentinel, not NULL. For prompt/config that is a
string. For `strictness_level`, FR-I-01 says the scale is Lenient / Standard / Strict; legacy rows were graded before any
scale existed, so recording `STANDARD` would be a claim we cannot back. I use a fourth value `LEGACY`, excluded from
QA-ACC-07's monotonicity query by definition. **Decision for Feature Lead** (default `LEGACY`).

---

## 4. Version registry (`ai_processor/versioning.py`)

### 4.1 Prompt version

Prompts are files loaded at import (`_load_prompt`, `services.py:92`), versioned only by filename suffix
(`GRADING_ASSIGNMENT_PROMPT_5.txt`). The registry computes, at import,
`prompt_version = "<stem>@<sha256[:8] of file text>"`, e.g. `GRADING_ASSIGNMENT_PROMPT_5@1a2b3c4d`.

- The **hash is the truth; the stem is a label.** A file edited without renaming changes the hash, so "same name, different prompt" cannot happen unnoticed (that is BE-I-06's failure mode).
- Which prompt(s) does a grading run use? The grading prompt is used at three sites
  (`services.py:2556, 2816, 3787`). Answer extraction (`ANSWERS_EXTRACTION_PROMPT_HTML_4`) feeds the run but is an
  input step. **Decision:** `prompt_version` records the grading prompt only; extraction prompt id goes in `run_meta`
  when extraction ran in this run. **[UNVERIFIED]** whether extraction is part of one `grade_student_submission` call or a
  separate earlier task (it appears separate; confirm).
- Teacher custom instructions are appended by `_custom_instructions_block` (`:2556`). The base version does not describe them.
  `run_meta.custom_instructions = true/false`. No content or hash of the text is stored (it is teacher-authored free text).
- Deterministic-only runs (no model call): `prompt_version = "deterministic"`.

### 4.2 Config version

`GradingConfig` is a frozen dataclass snapshot of the **effective** values used for a run: chunk size, main model,
grading fallback list, second-opinion models and thresholds, evidence mode, schema-enabled flag, disagreement fractions,
answer-cache enabled/TTL. `config_version = f"{CONFIG_LABEL}@{sha256(canonical_json)[:8]}"`, with
`CONFIG_LABEL = "std-1"` a hand-bumped constant in code. Per X-1 the label changes only by reviewed commit; the hash also
reflects environment overrides (§0.5). **The `STANDARD` strictness of I-2 must reproduce exactly today's configuration
(same label and hash inputs), or I-2 must bump the label.** Recorded so I-2 cannot drift silently.

**CI guard (BE-I-06 made mechanical).** `test_grading_config_snapshot_pinned` hashes the snapshot of the **code defaults**
(not the environment) and compares it to a value pinned beside `CONFIG_LABEL`. Changing a default without bumping the
label fails CI, so "released through the same gate as application code" is enforced rather than remembered.

The snapshot is taken **once at the start of `grade_student_submission`** and used for both the cache key and the stamp, so
a mid-run settings change cannot split the two.

### 4.3 Model identity and fallback

`model_id` = `feedback["grading_model"]` when present, else `"deterministic"` if the run made no AI call, else `"unknown"`.
`fallback_used` = `model_id` not in `{MAIN_MODEL, "deterministic"}` (the served model differs from the intended primary).
The audit emitter (`audit/emitter.py::_emit_alertable_metrics` on `task/epic-a-land`) computes `model_fallback_rate` as
`model in GRADING_FALLBACK_MODELS`. The two predicates differ when a model outside both sets serves a run (`unknown`,
an unlisted provider route). **Decision:** the column uses the broader predicate above (anything not the primary counts),
and a test asserts that for every model in `GRADING_FALLBACK_MODELS` both predicates agree, so the metric is a subset of the
column and never contradicts it.
A chunked run can be served by different models per chunk **[UNVERIFIED]** (`services.py:2906` sets one
`grading_model` on the summary; which chunk's value wins is unread). The stamp takes the primary value and records the full
set in `run_meta.models_used`. Where `models_used` has more than one entry the run is flagged for the fallback metric
(NFR-MDL-03).

### 4.4 Cache key

`build_cache_key(..., model_name, assignment_id)` gains `prompt_version` and `config_version` in the hashed parts, in a
fixed position, with `CACHE_VERSION` bumped to `"v2"` in the same change. **Consequence:** one-time cold cache on deploy
(cost: extra grading calls for a few days; TTL is 3 days). Acceptable, flagged for the Feature Lead because it is a cost
event. The run's `run_meta.cached_questions` counts cache hits so a partially cached run is visible.

Two things a hit must not do: a hit written under version X must never satisfy a lookup under version Y. That is a unit
test on `build_cache_key`, plus a mutation (remove the version from the key → test fails).

---

## 5. Stamping and dual-write

### 5.1 Where provenance is produced

`grade_student_submission` (`ai_processor/services.py:3517`) is "the one choke point every grading path returns through"
(its own comment, `:3548`), and already post-processes the result via `_stamp_answer_provenance`. It builds the
`GradingConfig` snapshot at entry and, on return, attaches a `run_provenance` dict (`prompt_version`, `config_version`,
`model_id`, `fallback_used`, `second_opinion_model_id`, `run_meta`) **next to** the result, not inside it: return a small
`GradingOutcome(result, provenance)` or pop the key before persistence. **Decision:** the persistence function receives
provenance as a separate argument, so it can never end up inside `feedback` and never reaches
`_student_safe_feedback`. (That projection is a whitelist, `students/serializers.py:459`, so a leak is unlikely, but
"cannot leak" beats "unlikely".)
Callers of `grade_student_submission` other than the persistence path need a compatible shim: **[UNVERIFIED]** the caller
list; grep at build time.

### 5.2 The persist site

There is **one** AI-result persist point: `_populate_and_save_grade` (`students/services.py:292`), which ends in
`submission.save(update_fields=GRADING_RESULT_FIELDS)` (`:437`). Callers: `grade_engine`
(`assignments/tasks.py:134` grade-all, `:484` `grade_engine_async`).

New step after that save: `sync_grading_record(submission, provenance, source="AI_RUN")`:

1. In one `transaction.atomic()`: `UPDATE ... SET is_authoritative = false WHERE submission_id = ? AND is_authoritative` then `INSERT` the new row with `is_authoritative = true`. Two statements, ordered so the partial unique index never sees two `true` rows.
2. Failure handling: caught, logged (ids only), metric `grading_record_sync_failed` (via `audit.metrics.count`, lazy import, as B0). **A failed sync never fails the grade** (same principle as FR-A-11 / NFR-REL-05). The drift it can create is what §7 detects and heals.

**Why after the save and in its own transaction, not the same one.** `submission.save()` fires `post_save` →
`_recalculate_final_grade`, which takes `select_for_update` on the enrollment row (`classrooms/signals.py`). If the
sync ran inside one wrapping `atomic()`, that lock would be held for our extra statements too. NFR-PRF-07 says nothing
new goes inside that lock on the hot path. Cost of the choice: a crash between the two writes leaves a graded
submission without a run row until the sweep heals it. That is safe **only because no reader uses the new table in I-1**;
it must reach zero drift before I-2 moves any reader (exit criterion, §8). **[UNVERIFIED]** whether an ambient
transaction already wraps the persist (`ATOMIC_REQUESTS` is not set in settings; Celery tasks are not atomic by default;
confirm no `@transaction.atomic` above `grade_engine`).

**AI re-runs append, not overwrite.** Today a re-grade of a graded submission overwrites `score`/`feedback` in place. The
submission columns keep that behaviour (readers unchanged). The new table appends a row and demotes the old one, so
history exists from day one. Storage cost is one row of `feedback` JSON per re-run; re-runs are rare. This is
invisible to every user.

### 5.3 The in-place writers

| Site | Today | Dual-write |
|---|---|---|
| Teacher override, `update_grade` (`students/views.py:1052–1087`, `save(update_fields=[score, score_percentage, max_points, feedback, was_regraded, regraded_at, needs_review, review_reasons])`) | edits the submission in place | `UPDATE` the authoritative row: `score`, `score_percentage`, `max_points`, `feedback`, `teacher_overridden`, `overridden_at`, review fields. `ai_score` untouched |
| `mark_reviewed` (`students/views.py:1343–1353`, `.filter(needs_review=True).update(...)`) | `.update()`, no signals | `UPDATE` the authoritative row's `needs_review`, `review_reasons` |
| Publish (`students/views.py:1298`, `assignments/views.py:1696`) | `.update(is_published=True)` | **None.** `is_published` is a submission property, not a run property, and stays on `StudentSubmission` (FR-I-09's published/unpublished distinction lives there) |
| `formatted_grade_async` / `format_grade` | own columns | **None** (`formatted_grade` stays on the submission) |

All three mirrored writers go through one helper module (`students/grading_records.py`) so there is one place that
knows the column mapping.

**Un-mirrored writers that remain (found by the survey), by design in I-1:**

- `students/admin.py:6–14`: every grade column is editable in the Django admin. A superadmin edit is not mirrored. Options: make the grade columns read-only in admin (a behaviour change, small); or accept drift and let §7 report it. **Default: report-only in I-1**, decision for Feature Lead.
- `classrooms/scale_my_students.py:175–184`: benchmark/seed `bulk_create`. Not production; the parity command excludes rows without `graded_at`… **[UNVERIFIED]** whether seeded rows must be mirrored for the load tests I-2 will run.
- `assignments/tasks.py:691` `format_grade`: `submission.save()` **with no `update_fields`**, on an instance loaded before a slow AI call. That is a pre-existing stale-write bug (§9.1). I-1 does not touch it; but a dual-write built on the submission columns inherits the risk, so it is reported.

A test enforces the "no new un-mirrored writer" rule: an AST scan of non-test code for assignments/`update()` calls
naming any of the mirrored columns, allow-listing the sites above. Adding a writer without deciding fails CI. (Same
technique as B0's chokepoint test.)

### 5.4 Audit stamp (no new action)

On `task/epic-a-land`, `audit/metadata.py` already allow-lists `model`, `prompt_version`, `grading_config_version` and
`strictness` for `GRADING_COMPLETED`, but the only call site (`assignments/tasks.py:528`) sends only `model`. I-1 populates
the other three from the run provenance (§5.1) at that call site, three lines. `strictness` is `"STANDARD"` until I-2.
This is read-only metadata on an existing event: it is **not** behind the dual-write flag, so the audit trail carries the
stamp even while the table is dark. **No new `AuditAction`**, retention class unchanged. Because B1 and E1 also touch
this file, the change is kept to that one dict (see §10 item 16).

### 5.5 Feature flag

`GRADING_RECORD_DUAL_WRITE_ENABLED` (env-driven setting, default **False**, so the code ships dark per NFR-MNT-05; the
environment variable is set to true at deploy I-1b, after I-1a is verified). Off means the pipeline does not touch the new
table. Also the rollback lever for deploy I-1b. Not a kill switch for the registry or cache key
(those have no off state; their rollback is a revert).

---

## 6. Back-fill

**Definition of "graded":** `graded_at IS NOT NULL`. (`ai_graded_at` is set **before** the AI call,
`students/services.py:451`, so it also exists on runs that failed or are in flight; it is the wrong test.)

**Command:** `manage.py backfill_submission_gradings [--batch-size N] [--dry-run] [--max-rows N]`.

- Not a `RunPython` migration: row count is unknown (**[UNVERIFIED]**, 03a §8; must be measured on a production-shaped DB before deploy I-1c), and MIGRATIONS.md allows "a data migration or a one-off management command".
- Resumable and idempotent: selects submissions with `graded_at IS NOT NULL` that have **no** `SubmissionGrading` row, keyset-paginated by `id`, one short transaction per batch, no long-held locks.
- Concurrency with live dual-write: the insert is `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index, so a live run that already created the authoritative row wins and back-fill skips it. Never `UPDATE`s an existing row.
- Mapping: all mirrored columns copied as-is; `strictness_level = strictness_source = LEGACY`; `prompt_version = config_version = "legacy-pre-part1"`; `model_id` from `feedback["grading_model"]` else `"unknown"`; `second_opinion_model_id` from `feedback["second_opinion"]["model"]` if present; `fallback_used` false; `teacher_overridden` ← `was_regraded`; `is_authoritative = true`; `run_meta = {"backfilled": true}`.
- Prints counts (candidates, inserted, skipped, errors) and exits non-zero on any unexpected error.

Runs in deploy I-1c, after the flag has been on long enough that all recent runs are dual-written.

---

## 7. Parity checker

`manage.py check_grading_parity [--fix]`: for every submission with `graded_at IS NOT NULL`, the authoritative row must exist and
match the mirrored columns (score, ai_score, score_percentage, max_points, feedback, graded_at, confidence, review
fields, `teacher_overridden`). Reports counts per drift kind (missing row, mismatch per column, orphan row with no graded
submission, two authoritative rows: impossible by the index, asserted anyway). `--fix` re-syncs from the submission (the
submission columns are the source of truth in I-1). Emits `grading_parity_drift` as a metric. Run periodically by an
operator during I-1; the **I-2 exit criterion is zero drift on a full run**.

---

## 8. Test plan, mapped to acceptance criteria

| Requirement | Test |
|---|---|
| FR-I-04 "no graded submission lacks any of the four" | After a full grading run across strictness (STANDARD only in I-1), models (primary, forced fallback, deterministic-only) and assignment types (objective, mixed, handwritten/extraction path), sample every new row: `prompt_version`, `config_version`, `strictness_level`, `model_id` all non-empty and not a sentinel. Plus a DB check: a `SELECT` over new rows for `''`/NULL returns zero |
| FR-I-04 "legacy rows carry a sentinel, not a NULL" | Back-fill test: every back-filled row has `legacy-pre-part1`; a `WHERE prompt_version IS NULL` query returns zero |
| NFR-OBS-04 every model interaction records model and prompt version | Covered for grading by the above. Other AI features (extraction, generation, summaries) are **not** in I-1; flagged §10 |
| NFR-MDL-03 `fallback_used` recorded | Force the served model ≠ `MAIN_MODEL` (patch `response.model`); assert `fallback_used`; and that the fallback-rate metric input is emitted |
| Cache truth (§0.3) | Key differs when `prompt_version` or `config_version` differs; a cached evaluation under version X is not returned under Y; `cached_questions` counted |
| Dual-write parity | After grade, override, mark-reviewed: authoritative row equals the submission on every mirrored column |
| Re-run appends | Grade twice: two rows, exactly one authoritative, old one retained; DB rejects a manual second `true` (`IntegrityError`) |
| Sync failure never fails a grade | Make the sync raise (patch): the grade persists, the metric fires, parity reports the gap, `--fix` heals it |
| Idempotency under Celery redelivery (NFR-REL-01) | Deliver `grade_engine_async` twice; the `grading_state` claim already prevents double-run; assert one row per real run, none duplicated |
| Back-fill | Idempotent (second run inserts 0), resumable (kill mid-run, re-run completes), correct mapping, never touches an existing live row |
| "No un-mirrored writer" | AST scan test (§5.3) |
| No student exposure | No serializer, view or admin lists the new model's `prompt_version`/`config_version`/`model_id`/`run_meta` to a student (assert on the student-facing serializer field sets). No new API in I-1 |

### 8.1 Mutation matrix (NFR-TST-07)

| # | Mutation | Test that must fail |
|---|---|---|
| M1 | Drop `prompt_version` from the cache key | cache truth test |
| M2 | Drop `config_version` from the cache key | cache truth test |
| M3 | Stamp `model_id` from the intended model, not the served one | fallback test |
| M4 | Skip the sync call in `_populate_and_save_grade` | parity test |
| M5 | Skip the demote `UPDATE` before the insert | re-run test (unique violation) |
| M6 | Remove the partial unique index | duplicate-authoritative test |
| M7 | Let a sync exception propagate | sync-failure test |
| M8 | Back-fill uses `ai_graded_at` as the "graded" test | back-fill test (in-flight fixture row gets a bogus run) |
| M9 | Back-fill `UPDATE`s an existing row | back-fill vs live-write test |
| M10 | Put provenance into `feedback` | feedback-shape / student projection test |
| M11 | Override path forgets `teacher_overridden` | override parity test |
| M12 | Remove `LEGACY` from the CHECK | back-fill test |

Counts recorded in the evidence doc (`docs/evidence/epic-i1/`).

### 8.2 Roadmap §7 checklist

| # | Item | I-1 answer |
|---|---|---|
| 1 | Audit actions added | **None.** Three already-allow-listed metadata keys are populated on `GRADING_COMPLETED` (§5.4). No enum or `retention_class_for` change |
| 2 | Student deletion/export walk (NFR-CMP-04) | Adds `SubmissionGrading` (cascade from `StudentSubmission`; it carries `score`, `feedback`, `graded_by_id`). No export or deletion walk exists on beta (`docs/DATA_PRIVACY_AND_SECURITY_COMPLIANCE.md` §6; roadmap Q4 has no owner), so I-1 delivers the cascade edge, a test that deleting a submission or student removes its runs, and this line for whoever owns the walk. It does not build the walk |
| 3 | Gates | §8.3 below |
| 4 | Migration class and three-deploy schedule | One migration, **additive** (create table). Deploy 1 is I-1a/b/c (§2); deploy 2 (readers) and 3 (contract, `# expand-contract-step: contract`) belong to I-2 |
| 5 | Ships dark | `GRADING_RECORD_DUAL_WRITE_ENABLED`, default False (§5.5) |
| 6 | Role-by-resource matrix (NFR-SEC-01) | See below |

| Resource | Student | Teacher | School Admin | Super Admin |
|---|---|---|---|---|
| `SubmissionGrading` rows | none (no endpoint, not in any student serializer) | none (no endpoint) | none | Django admin only if registered; **decision: not registered**, so all four are denied by absence |
| `backfill_submission_gradings` / `check_grading_parity` | | | | operator shell only, not reachable over HTTP |

I-2 adds the read surface; its tenancy column is `submission.assignment.course.teacher` for teachers, the school for
admins, and the student's own submission for students (03a §6). Rows for that surface belong to the I-2 plan.

### 8.3 Gate applicability (`10 Gates.md`, Gates 1–9)

| Gate | Applies | Basis |
|---|---|---|
| 1 Baseline / regression | Yes | Full `students/`, `ai_processor/`, `assignments/`, `classrooms/`, `dashboard/` suites before/after. Readers are unchanged, so the whole reader suite must be **identical** in results |
| 2 Mutation | Yes | §8.1 |
| 3 Concurrency | Yes | Real threads, barrier-synchronised, real Postgres: (a) live grading + back-fill on the same rows; (b) two concurrent AI runs for one submission (should be blocked by the claim; assert no double-authoritative even if the claim were bypassed); (c) override racing a re-run |
| 4 Adversarial | Yes | Attempt: two `true` rows via ORM/raw; back-fill overwriting a live row; provenance forged via a submission's own text (an answer containing "prompt_version" must not reach the stamp); student reading any new data |
| 5 Failure / recovery | Yes | Sync raises; DB error mid-batch in back-fill; process killed between save and sync (the healed-by-sweep case); Redis down during a run (cache miss, run proceeds). Provider timeout: run fails as today, no row written |
| 6 Stress / scale | Yes | Back-fill at production-shaped volume (row count to be measured; use ≥ that scale and record rows/sec and lock waits); dual-write overhead on the grading hot path measured before/after (extra queries per grade: expect +2, report the wall time), and confirm no added time inside the enrollment lock |
| 7 Real infrastructure | Yes | Real Postgres 16 semantics for the partial unique index and `ON CONFLICT` (local is 18.6; NFR-TST-11), real Redis for the cache, real Celery worker for one end-to-end run |
| 8 Live / end-to-end | Yes | One real submission through request → Celery → OpenRouter (test key) → DB on the beta environment: assert the row, the versions, the served model. Subject to the credit/provider cost being approved **[NEEDS: cost approval and a beta window]** |
| 9 Security / isolation | Partly | No new endpoint or serializer. Verify student and other-teacher isolation by asserting nothing exposes the model; teacher/school/billing isolation matrices: **N/A**, nothing added or changed. Run the existing isolation suites for regression only |

Full-suite runs need a slot from Integration & Release's queue (machine load policy). Targeted module tests do not.

---

## 9. Findings from the survey (reported, not fixed in I-1)

These are pre-existing and matter to I-2, E1 or integrity. Each needs an owner.

1. **`format_grade` can clobber grades.** `assignments/tasks.py:670–691`: the task loads the submission, makes a slow AI call, then `submission.save()` **with no `update_fields`**. Any grade, review or publish state written between the load and the save is overwritten from the stale instance. Every other pipeline writer uses `update_fields`. **[UNVERIFIED]** whether `format_grade` is still called (the async variant `formatted_grade_async` uses `update_fields=["formatted_grade"]`). If live, this is an integrity defect independent of Part 1. **Route to Feature Lead / Platform Hardening.**
2. **Dead / wrong reads.** `ai_feedback` is never written (dead column, dropped at I-2 contract or left). `dashboard/views.py:719–729` filters on `feedback__grading_evaluation__grading_confidence_score`, but the stored key is `feedback["grading_confidence"]`, so that average is very likely always empty **[UNVERIFIED against data]**.
3. **No unpublish path exists** (survey: nothing sets `is_published=False` except the admin). FR-I-09 requires "a published/unpublished distinction the frontend must honour"; the distinction exists as a flag, but there is no state transition back.
4. **`is_published` is applied inconsistently.** `compute_final_grade` ignores it (unpublished grades feed `final_grade`); student dashboards and `dashboard/services.py` Python-side filters count only published; several dashboard averages count both (`dashboard/services.py:1165`, `dashboard/views.py:3112, 3363–3372, 3491`). E1 must decide, deliberately, whether "byte-identical" preserves this.
5. **Reader inventory for I-2 / E1** (grouped, from the survey). Weighted-average formula: 1 site (`classrooms/signals.py compute_final_grade`, `Sum(score)/Sum(Coalesce(max_points, assignment.total_points))`, `graded_at` and `score` not null; recalculation receivers on submission `post_save`/`post_delete`; `.update()` paths skip it). Aggregations over `score_percentage`/`score`: ≥ 8 sites (listed in §0.6). `graded_at` filters/annotations: ~40. Serializers exposing the fields: `students/serializers.py` (four classes), `assignments/serializers.py`, `classrooms/serializers.py:455–468`, `dashboard/serializers.py`. Exports: none found (`assignments/pdf_*` do not touch these fields; NFR-CMP-04's export does not exist). No raw SQL, materialized view or `.raw()` touches the columns (survey). Index note: the only indexes on these columns are `score_percentage`, `needs_review`, `review_severity`, `review_tier`, `was_regraded`, `grading_state` and `graded_at`; nothing on `score`, `is_published`, `grading_confidence`. The new table needs equivalents before readers move.

---

## 10. Open items and unverified claims

| # | Item |
|---|---|
| 1 | **Feature Lead decisions:** (a) vocabulary: `teacher_overridden` vs regrade (§0.2); (b) `LEGACY` strictness sentinel (§3); (c) admin edits report-only vs read-only (§5.3); (d) cold-cache cost of the cache-key change (§4.4); (e) AI re-runs append from I-1 (§5.2); (f) size: M ≈ 2 wk, not ~1 wk |
| 2 | Whether the answer-extraction prompt runs inside one `grade_student_submission` (§4.1) |
| 3 | Which chunk's `grading_model` wins on a multi-chunk run (§4.3) |
| 4 | Whether `response.model` is always set by OpenRouter (§0.4) |
| 5 | No ambient `atomic()` above the persist (§5.2) |
| 6 | `trace_id` format and the correct import (`AutoGrader.request_context` vs `audit.context` on `task/epic-a-land`) |
| 7 | Production row count of graded submissions (sizes the back-fill; must be measured, not assumed) |
| 8 | Every student-deletion path uses the ORM cascade (§3) |
| 9 | Callers of `grade_student_submission` besides the persist path (§5.1) |
| 10 | Whether seeded/benchmark rows need mirroring (§5.3) |
| 11 | Whether `format_grade` is live (§9.1) |
| 12 | NFR-OBS-04 for non-grading AI features (extraction, generation, summary) is outside I-1; someone must own it |
| 13 | Gate 8 needs a cost approval and a beta window |
| 14 | Soft dependency on Epic A for the metrics module; on beta without it the metrics calls are no-ops and only logs remain |
| 15 | Estimate excludes independent verification and CI queue time |
| 16 | File ownership with B1 and E1: I-1 owns writers in `students/services.py` and `students/views.py` and a 3-line metadata edit at `assignments/tasks.py:528`; B1 and E1 own the rest of the grading task path. Agreed boundaries to be confirmed with the B1 plan author |
| 17 | Decision (Feature Lead): flag default False and enabled by env at I-1b, versus default True as first drafted (§5.5) |

**Effect on the roadmap:** I-1 moves from about 1 week to about 2. The 05 §5 schedule had about 2 weeks of slack, so
this leaves about 1 week. If E3 (`FeedbackRevision`) depends on this table (it does), E3's start is gated on I-1b.
