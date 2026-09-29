# H-1 step 4 — removing the legacy wildcard cache invalidation: evidence

Backlog item: `docs/HARDENING_BACKLOG.md` H-1. Plan:
`docs/H1_STAGE3_WILDCARD_REMOVAL_PLAN.md` §2 and §4 step 4. Steps 1–3:
`docs/evidence/H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md`. Owner approval
for step 4 as a separate landing: 2026-09-28.

**Status: IN PROGRESS.** This file is written stage by stage; the inventory
(§2) was recorded before anything was deleted.

## 1. Stage 1 — the status-summary family put on generations first

See §3 below once written.

## 2. Inventory, recorded before deletion (at `ffe90cc`)

Every non-test reference to the wildcard mechanism. Line numbers are at
`ffe90cc` (stage 1 committed, nothing removed yet). Searched for
`delete_cache_patterns`, `delete_pattern(`, `batched_cache_invalidation`,
`_delete_now`, `_pending_patterns`, the warn-once flag, `*_PATTERNS`
constants, `scan_iter`/`iter_keys`/`keys(` against the cache.

### 2.1 Definitions (2) and helper machinery

H-4 recorded up to four drifted copies. Two remain: `students/signals.py`
and `assignments/signals.py` had already been switched to import the shared
helper (their comments at lines 14–20 and 28–35 record it), as has
`users/signals.py`.

| # | Where | What | Disposition |
|---|---|---|---|
| D1 | `AutoGrader/cache_utils.py:45` | `delete_cache_patterns` (shared) | removed, with the whole module |
| D1a | `AutoGrader/cache_utils.py:27` | `_delete_now` (the `cache.delete_pattern` loop, line 40; silently no-ops when the backend lacks `delete_pattern`, line 34) | removed |
| D1b | `AutoGrader/cache_utils.py:24, 64` | `_pending_patterns` ContextVar and `batched_cache_invalidation` (no production caller) | removed |
| D2 | `classrooms/signals.py:45` | local `delete_cache_patterns` copy (`cache.delete_pattern`, line 77) | removed |
| D2a | `classrooms/signals.py:35, 62–73` | warn-once path (`_warned_backend_lacks_delete_pattern`) | removed |
| D2b | `classrooms/signals.py:37` | `CACHE_UNAVAILABLE_ERRORS`, used only by D2's `except` | removed |

### 2.2 Call sites (11) and what replaces each

"Replaced by" is the generation bump that already runs in the same
function, immediately before the wildcard call. Each was put there and
proven by steps 1–3 with the wildcards disabled (the Stage 3 evidence
§2–§5); step 4 removes only the wildcard line.

| # | Where | Receiver / function | Patterns cleared | Replaced by (already in place) |
|---|---|---|---|---|
| C1 | `classrooms/signals.py:156` | `clear_school_cache` (School save/delete) | `*superadmin*`, `*schooladmin*`, `schools:*`, `courses:*`, `sessions:*` | `bump_many` `sch(school)`, `anysch`, `global` |
| C2 | `classrooms/signals.py:198` | `clear_session_cache` (Session) | `*superadmin*`, `*schooladmin*`, `*school*`, `sessions:*`, `courses:*`, `assignments:*`, `studentsubmissions:*` | `usr` of teacher, created_by, the school's admins and teachers, every superadmin; `sch`, `global` (G6) |
| C3 | `classrooms/signals.py:212` | `clear_course_cache` (Course) | 12 patterns incl. `*user*`, `*school*`, `*studentadmin*`, `*teacheradmin*` | `_course_scopes`: `crs`, teacher `usr`, `sch`, every enrolled student's `usr` (G5); `global` |
| C4 | `classrooms/signals.py:259` | `clear_student_course_cache` (StudentCourse) | 11 patterns incl. `*user*`, `*studentadmin*` | student `usr`, `global`, `_course_scopes` (G2, G5) |
| C5 | `classrooms/signals.py:279` | `clear_topic_cache` (Topic) | 8 patterns | `_course_scopes` + `global` (G5) |
| C6 | `students/signals.py:73` | `invalidate_submission_caches` (receiver + 3 `.update()` paths incl. the grading claim) | `SUBMISSION_CACHE_PATTERNS` (7) | `_bump_submission_scopes`: student and course-teacher `usr`, `crs`, `sch`, `global` (P1) |
| C7 | `students/signals.py:110` | `invalidate_submission_caches_bulk` (publish-all-grades) — the site the Verification Engineer flagged | `SUBMISSION_CACHE_PATTERNS` (7) | every student's `usr` + teacher `usr`, `crs`, `sch`, `global` in one pipelined call (G3) |
| C8 | `students/signals.py:120` | `clear_batch_upload_session_cache` (BatchUploadSession) | `studentsubmissions:*`, `assignments:*` | nothing needed: plan §1 table, last row — no cached payload reads BatchUploadSession (re-checked: no serializer or cached view renders it; `batch_session` is not serialised). The receiver is deleted, as the plan says. |
| C9 | `assignments/signals.py:195` | `clear_assignment_cache` (Assignment) | 8 patterns incl. `*user*`, `*studentadmin*` | `_bump_assignment_scopes`: `crs`, COURSE teacher `usr`, `sch`, `global`, every enrolled student's `usr` (G1) |
| C10 | `assignments/signals.py:219` | `clear_assignment_generation_session_cache` | `*assignmentgenerationsession*` | owner `usr` (G9) |
| C11 | `users/signals.py:275` | `clear_user_cache` (CustomUser and Settings) — the `*user*` sweep | 9 patterns incl. `*user*`, `*course*`, `*settings*` | user `usr`, `anyusr`, `global`, every superadmin `usr` (G8), viewer fan-out (G4, item 7, gap #3/#5) |

### 2.3 Constants (1)

| Where | What | Disposition |
|---|---|---|
| `students/signals.py:26` | `SUBMISSION_CACHE_PATTERNS` | removed (plan §2) |

### 2.4 Kept: the plan's one documented exception

| Where | What | Why it stays |
|---|---|---|
| `assignments/pdf_cache.py:173` | `cache.delete_pattern(f"assignmentpdf:<ver>:<assignment id>:*")` in `invalidate_assignment_pdfs` | Plan §2: "not a legacy wildcard". Exact prefix of ONE assignment's own rendered PDFs; the key is also versioned by `updated_at`; no other family shares the prefix. Design doc family 34. |

Not invalidation, so not in scope, but reported so nothing is missed:

* `ai_processor/grading_cache.py` — content-addressed (design family 35);
  no delete of any kind.
* `AutoGrader/redis_test_hygiene.py:97, 113` — `scan_iter` in the TEST
  RUNNER's per-process namespace cleanup (`gaplus-t<pid>:*`), used only by
  `AutoGrader.redis_test_runner`; never loaded by production.
* `AutoGrader/cache_generation.py:260` — `get_client` for the pipelined
  `SET NX` + `INCR` bump; no SCAN.
* `AutoGrader/settings.py:1299` — `DJANGO_REDIS_SCAN_ITERSIZE`; still used
  by the PDF exact-prefix clear, so kept (comment updated).

### 2.5 Every cache write in non-test code, checked for a remaining raw key

A family keyed without a generation and cleared only by a wildcard would go
silently stale when the wildcards go, so every `cache.set` / `cache.add` /
`get_or_set` in non-test code was listed and its key traced:

* `dashboard/views.py`: 22 `cache.set` sites. 21 build their key with
  `versioned_key`; the 22nd, `status_summary`, was raw until stage 1
  (`ffe90cc`) and is now versioned.
* `users/mixins.py` (`UserCacheMixin`, 2 sites), `users/views.py` (2),
  `students/views.py` (1), `classrooms/views.py` (1): all `versioned_key`.
* Not response caches, never matched by a legacy pattern, and not affected:
  `assignments/pdf_cache.py` (exception above), `ai_processor/grading_cache.py`
  (content-addressed), `billing/overage_pricing.py` (Stripe price, TTL),
  `billing/stripe_service.py` (portal configuration id), `users/throttling.py`
  (failure budget), `AutoGrader/health.py` (probe), the `cache.add` locks in
  `billing/` and `users/middleware.py`, and the counters themselves.

Result: after stage 1, no response-cache family depends on a wildcard.
