# 00 — Repository state

**Phase 0 of the Phase 2 Part 1 backend architecture engagement.**
Established 2026-09-10 by direct inspection. Nothing in the application tree
was modified. Every figure below was produced by a command run in this
session; where a figure could not be produced, it says so.

Evidence buckets are used throughout, per the operating rules:
**Confirmed** (read or ran it), **Assumed** (believed, with the reason),
**Must verify** (could not be established here).

---

## 0. One thing to read first

The working tree is **shared with at least one other active automation
session**, and during this phase it was caught in a **mutated state**. That is
finding F-1 below and it conditions everything else, including the test
baseline. It is not a hypothetical.

---

## 1. Branch and tree

| Fact | Value | Bucket |
|---|---|---|
| Branch analysed | `beta` | Confirmed |
| HEAD | `a4e4ebe` — *"Stop the gevent ORM test depending on the developer's own database"* | Confirmed |
| HEAD date | 2026-09-06 15:22:14 +0100 | Confirmed |
| Commits `main..beta` | **151** | Confirmed |
| Uncommitted paths | **89** (51 tracked-modified, 38 untracked) | Confirmed |
| `ENVIRONMENT` | `local` | Confirmed |

### Branch correction

The engagement brief was written against `append-only-audit-tables`. The owner
corrected this mid-phase to **`beta`**, and the analysis was re-baselined
there.

`beta` is a strict superset of `append-only-audit-tables`: 3 commits ahead,
0 behind, touching only three files
([assignments/pdf_renderer.py](../../../assignments/pdf_renderer.py),
[assignments/tasks.py](../../../assignments/tasks.py),
[assignments/tests_pdf_renderer_gevent.py](../../../assignments/tests_pdf_renderer_gevent.py)).
All three were clean in the working tree, so the switch moved only those
files and **all 89 uncommitted paths were preserved** — verified by comparing
`git status --porcelain | wc -l` before (89) and after (89) the checkout.

Two stale `beta` worktrees under another session's scratchpad
(`.../16ed2c47-.../scratchpad/{hotfix,baseline}-beta`) were holding the branch
checked out; both directories no longer existed, so `git worktree prune`
cleared them. That is the only git-state change made in this phase, it was
authorised by the owner, and it touched no file content.

> **Operating-rule note.** Rule §7 forbids altering the working tree. The
> branch switch is an explicit, owner-approved exception, requested and
> confirmed before it was performed. No other mutation was made.

### F-2 — `main` does not reflect reality (Confirmed)

`main` is **151 commits behind** `beta`. Every claim in this document, and
every design in the documents that follow, is about `beta`. Reading `main`
will mislead.

---

## 2. F-1 — The working tree is shared, and was caught mutated

**Confirmed, with timestamps. This is the first finding of the engagement.**

A concurrent automation session was running an H-1 stage-2 **mutation-testing**
script against this same working directory throughout Phase 0. The script
deliberately damages source files, runs a targeted test, and restores from a
`/tmp` backup — by design, that means the tree is *intermittently and
invisibly wrong*.

Observed directly:

| Time | Observation |
|---|---|
| 15:44:52 | Script records its restore baseline (`/tmp/h1_stage2b.md5`) |
| ~15:45:5x | **`assignments/signals.py` observed mutated** — `_bump_assignment_scopes(instance)` stripped from the `clear_assignment_cache` receiver; `md5sum -c` FAILED for that file |
| 15:46:03 | File restored; checksum matches baseline again |
| 15:50:08 | Script exits; all three files verified restored (`dashboard/views.py`, `assignments/signals.py`, `students/signals.py` all `OK`) |

The mutated file is [assignments/signals.py:128](../../../assignments/signals.py#L128) —
the call whose removal makes dashboard family 32
(`assignment_activity_<school>_<year>`) permanently stale.

**Why this matters beyond hygiene.** A first attempt at the test baseline was
started at 15:44:40, i.e. *inside* that window. It was killed and re-run from a
verified-clean tree at 15:50:09 for exactly this reason. Any measurement taken
from this repository without first checking `md5sum -c /tmp/h1_stage2b.md5`, or
equivalent, is untrustworthy.

**On this occasion the restore succeeded.** That is worth stating plainly
because the project's own operating notes record that a "restored" mutant has
previously survived into the working tree more than once. The restore is
verified here, at 15:50:08, by checksum rather than by the script's own claim.

**Consequence for Part 1 planning:** Part 1 cannot begin against a working
tree that other processes mutate in place. This reinforces, and is
independent of, F-3.

---

## 3. F-3 — 89 uncommitted paths, including the project's own tracking documents

**Confirmed.** The onboarding summary estimated "~85 changed or new files".
The actual figure is **89**.

The composition matters more than the count. **38 paths are untracked**, and
they include work that is load-bearing for Phase 2 planning:

| Untracked path | Why it matters |
|---|---|
| `docs/HARDENING_BACKLOG.md` | The tracking document for H-1…H-8, including all acceptance criteria |
| `docs/H1_CACHE_INVALIDATION_DESIGN.md` | The approved Option-D cache design, 607 lines |
| `docs/decisions/` | ADR directory (currently one file) |
| `docs/backend/phase 2/` | **Every Phase 2 Part 1 input document** |
| `AutoGrader/cache_generation.py` | The H-1 generation-counter implementation |
| `classrooms/services/` | A new service package |
| `billing/credit_reversal.py`, `billing/disputes.py`, `billing/payment_refunds.py` | New billing subsystems |
| `billing/migrations/0061`–`0066` | **Six unmerged migrations** |
| ~20 new test modules | The evidence for the hardening work |

**These files exist in exactly one place: this working tree.** They are not in
`beta`, not in `origin`, not in any branch. An accidental `git clean`, a
disk failure, or a checkout by another session ends the H-1 design, the
hardening backlog, and the Phase 2 inputs simultaneously.

### Recommendation (carried into D1 and the sequencing section)

**The working tree must land before Part 1 begins.** The reasoning is not
tidiness:

1. **The plan cannot be reviewed against a moving target.** The gap register
   in Phase 1 cites file and line. Uncommitted, concurrently-mutated files
   make every citation provisional.
2. **Six unmerged billing migrations** set the migration dependency order that
   every Part 1 migration must build on. Their numbering is not final until
   they land.
3. **H-1 stage 2 is in flight in this tree** (see §6), and Part 1 adds a
   fourth counter dimension to that design. Designing against an
   unlanded, actively-changing implementation is designing against a guess.
4. **The Part 1 inputs and the hardening backlog are untracked.** Losing the
   tree loses the requirements.

This is a finding, not a decision — the sequencing cost of landing 89 paths
belongs to the owner, and is put to them in `02_decision_request.md`.

---

## 4. F-4 — The Phase 2 inputs are not where the brief says they are

**Confirmed.** The brief specifies `docs/phase2/*`. That directory did not
exist. The inputs are at **`docs/backend/phase 2/`** (note the space), and the
set is **larger** than the brief anticipated:

| File | Present | Note |
|---|---|---|
| `Phase 2 Part 1 Backend Requirements.docx.pdf` | ✅ | The binding document |
| `Phase 2 Part 1 School License Architecture.png` | ✅ | |
| `Phase 2 Part 1 Individual Architecture.png` | ✅ | |
| `Phase 2 Part 1 QA Testing Requirements.docx` | ✅ | **`.docx`, not `.pdf`** — BE-A-06's reason-code catalogue depends on it |
| `Phase 2 Part 1 Frontend Requirements.docx` | ✅ | **`.docx`** |
| `Phase 2 Founder Approval Checklist.docx.pdf` | ✅ | **Not mentioned in the brief.** Read in Phase 1 — an approval checklist may carry binding acceptance criteria |

Two inputs are `.docx` and will need conversion to be read reliably. Deliverables
are being written to `docs/phase2/architecture/` as the brief directs.

---

## 5. Test baseline

> **Status: run in progress at the time of writing.** Populated below on
> completion. Reported as actual counts and actual exit status, per the
> operating rules — no result is asserted before the run finishes.

### Method

The project's documented CI command, from
[.github/workflows/tests.yml:112](../../../.github/workflows/tests.yml#L112):

```
coverage run manage.py test --noinput
```

Run against a **session-unique database** (`test_gap_sess_f17c573c`) so it
cannot collide with the concurrent session identified in F-1. `ENVIRONMENT`
was `local`; no production or QA credential was used; `DATABASE_URI_LOCAL` was
overridden by environment variable only, and no file in the tree was edited to
achieve it.

Started **15:50:09**, from a tree verified clean by checksum at 15:50:08.

### Environment divergence from CI (Confirmed) — read before trusting any result

| Component | This machine | CI (`tests.yml`) | Risk |
|---|---|---|---|
| PostgreSQL | **18.6** | **16** | Two major versions apart. Planner and locking behaviour differ; a migration-safety or concurrency result here does not transfer to CI or production |
| Redis | **8.0.5** | **7** | |
| `maxmemory-policy` | `noeviction` | (default) | Matches the H-1 design's stated local value — the counter-eviction failure mode cannot occur locally either way |
| Python | 3.12.10 | 3.12 | Aligned |
| Django / DRF / Celery | 5.2.6 / 3.16.1 / 5.5.3 | from `requirements.txt` | Aligned |

**This divergence is itself a finding.** The production stack is Postgres 16
(per CI). Local development and any measurement taken here run Postgres 18.
Concurrency, locking and query-plan evidence gathered locally must be
re-confirmed on 16 before it is quoted as production behaviour.

### Environment warning surfaced at start (Confirmed)

```
billing.W001  PlanFeature catalogue is missing (or not marked
is_gating_feature=True for) gating key(s):
['AI_PROMPT_ANALYTICS_SUMMARY', 'AI_PROMPT_ASSIGNMENT_CREATION']
```

Every user in this environment is **denied** those two AI features until
`manage.py seed_plan_features` is run. Relevant to Part 1: the epics add AI
features, and the gating catalogue is evidently a seeded artefact that can be
absent. Whether Part 1's new AI surfaces must appear in `AI_FEATURE_GATING_MAP`
is a Phase 1 question.

### H-2 reproduction

`docs/HARDENING_BACKLOG.md` records H-2: the suite passes and still exits
non-zero, because threaded / `LiveServerTestCase` suites leak connections and
teardown fails with *"database is being accessed by other users"*.

A pre-run check found **1 lingering `test_*` connection** already open on this
server before the baseline started — consistent with H-2, though attributable
to the concurrent session rather than to this run. Whether H-2 reproduces here
is recorded with the result below.

### Result — Confirmed, from an actual run

```
Ran 3671 tests in 3074.000s

OK (skipped=12)
Destroying test database for alias 'default'...
=== EXIT STATUS: 0 ===
```

| Metric | Value |
|---|---|
| Tests collected and run | **3,671** |
| Failures | **0** |
| Errors | **0** |
| Skipped | 12 |
| **Exit status** | **0** |
| Wall time | 3,074s (**51 minutes**) |
| Window | 15:50:09 → 16:42:35, 2026-09-10 |

**The suite is green on `beta` + the uncommitted tree.**

### H-2 did NOT reproduce — Confirmed

This is a positive finding and it contradicts the current text of the
hardening backlog.

`docs/HARDENING_BACKLOG.md` records H-2 as *"The suite passes and still exits
non-zero, so CI would go red on a green run,"* with teardown failing on
*"database is being accessed by other users — There are 13 other sessions using
the database."*

Measured here, on the **full** suite (not the five-app subset H-2 was observed
against):

| H-2 acceptance criterion | Result |
|---|---|
| Full suite exits **0** | ✅ **0** |
| No lingering `test_*` connections in `pg_stat_activity` after a run | ✅ **0** |
| No leftover test databases | ✅ **0** |

Both post-run figures were queried directly against `pg_stat_activity` and
`pg_database` after teardown completed.

**Do not close H-2 on this evidence.** Three reasons:

1. H-2's own acceptance criterion is **three consecutive clean full runs**.
   This is one.
2. This run was on **Postgres 18.6**, not the Postgres 16 that CI and
   production use. Connection-teardown timing is exactly the kind of behaviour
   that can differ across major versions.
3. The threaded suites H-2 blames (`users/tests_activity_middleware_load.py`,
   `users/tests_login_lockout.py`, `students/tests_grading_idempotency.py`,
   `assignments/tests_load.py`, `assignments/tests_security.py`) all ran here,
   so the trigger was present — but the fix may already have landed in the
   uncommitted tree via
   `classrooms/tests_concurrency_and_resilience.ThreadSafeTransactionTestCase`,
   which H-2 itself names as the pattern that fixes it.

**Recommended:** re-run twice more on Postgres 16 before H-2 is closed. Until
then its status is *"not reproduced on one full local run"*, not *"fixed"*.

### Caveat on completeness of this baseline — Confirmed

The concurrent session (F-1) **added three new files to the working tree while
this run was in flight**: `billing/overage_pricing.py`,
`billing/management/commands/reconcile_overage_prices.py`, and
`billing/tests/test_overage_price_drift.py`.

Django collects tests at start-up, so **`test_overage_price_drift.py` was
almost certainly not included in the 3,671**. The baseline is therefore a
correct measurement of the tree *as it stood at 15:50:09*, and is already
slightly behind the tree as it stands now. The working-tree count moved
**89 → 93** during this phase for the same reason.

This does not invalidate the result. It does mean the number must be re-taken
after the tree lands (Stage 0), and it is a second concrete instance of why
F-1 and B-2 block implementation.

---

## 6. F-5 — H-1 stage 2 is in flight *right now*, so its stated progress is stale

**Confirmed.**

[docs/H1_CACHE_INVALIDATION_DESIGN.md:571](../../../docs/H1_CACHE_INVALIDATION_DESIGN.md#L571)
states:

> **Progress: 10 of 33 applicable families migrated (30%); 2 recorded N/A.**
> **Stage 3 is blocked until every row above reads DONE or N/A.**

That figure was accurate for the `UserCacheMixin` block (families 1–10). It is
**already out of date**: `versioned_key(` now has call sites in
[dashboard/views.py](../../../dashboard/views.py) as well as
[users/mixins.py](../../../users/mixins.py), and the concurrent session's
mutation script was specifically exercising families **30–33** — the four
dashboard responses that no invalidation mechanism reaches today
(`teacher_performance`, `teacher_detail`, `assignment_activity`,
`department_overview`).

**Must verify:** the exact DONE/TODO count at the moment Part 1 planning
freezes. It is moving during this engagement and cannot be quoted from the
design document.

**Why Part 1 cares.** Epic F introduces Departments, a **fourth ownership
dimension** with its own invalidation surface, alongside the existing
`cachegen:usr` / `cachegen:sch` / `cachegen:crs` namespaces. Part 1 will also
add new cache families to the 33-family checklist that gates stage 3. The
H-1 design therefore needs a revision *before* stage 3 removes the wildcards,
and Part 1 sequencing must account for it. Carried into the gap register and
the sequencing section.

Note also that the `cachegen:` namespace is **load-bearing while the two
stages coexist**: the design document records that the earlier `gen:` naming
was itself destroyed by the legacy `*user*` / `*school*` / `*course*` globs,
reviving stale entries. Any new key family Part 1 adds must not reintroduce a
matched substring while wildcards remain.

---

## 7. Scale — where the onboarding summary is materially wrong

Measured this session. **Confirmed** unless noted.

| Metric | Onboarding summary | Measured | Delta |
|---|---|---|---|
| Tests | "~1,500" | **3,671 collected and run** (3,659 `def test_` across 206 files) | **2.4×** |
| `billing` LOC (non-test) | "~30k" | **42,531** | +42% |
| `ai_processor` LOC (non-test) | "~14k" | **16,517** | +18% |
| `dashboard` LOC (non-test) | "~8k" | 8,680 | aligned |
| Uncommitted files | "~85" | **89** | close |

The authoritative figure is now the runner's own: **3,671 tests collected and
run**, confirmed in §5. The brief's "~1,500" understates the regression surface
by **2.4×**, and **estimating Part 1 test effort from it would understate the
work by more than double.** A full suite run costs **51 minutes**, which is
itself a planning input: it sets the floor on CI feedback latency for every
Part 1 change.

### Migrations by app (Confirmed)

| App | Migrations | | App | Migrations |
|---|---|---|---|---|
| `billing` | **66** (+6 uncommitted) | | `students` | 26 |
| `assignments` | 38 | | `classrooms` | **16** |
| `users` | 36 | | `ai_processor` | 5 |
| `dashboard` | 2 | | `grading`, `ocr_processor` | **0** |

`grading` and `ocr_processor` having zero migrations is consistent with the
documented "empty stub" status. `classrooms` — which Epics D, E and F all
extend — has the smallest migration history of the substantive apps, at 16.

---

## 8. Documents read in this phase

| Document | Read | Note |
|---|---|---|
| [docs/backend/README.md](../../backend/README.md) | ✅ | Trace order and the root-doc contradiction table |
| [docs/backend/glossary.md](../../backend/glossary.md) | ✅ | See §9 |
| [docs/HARDENING_BACKLOG.md](../../HARDENING_BACKLOG.md) | ✅ | H-1…H-8, 13-point verification standard |
| [docs/H1_CACHE_INVALIDATION_DESIGN.md](../../H1_CACHE_INVALIDATION_DESIGN.md) | ✅ | Option D; §571 progress figure now stale (F-5) |
| [docs/ops/postgres-guard-rails.md](../../ops/postgres-guard-rails.md) | ✅ | See §10 |
| [.example.env](../../../.example.env) header | ✅ | Blank ≠ unset; required keys fail at import |

---

## 9. First evidence on Conflict #1 (Session ownership)

[docs/backend/glossary.md:15](../../backend/glossary.md#L15) states:

> **Session** *(this codebase)* — an **academic period** … **Owned by either
> one teacher or a school.**

That is a **two**-model ownership rule in the documentation, against three in
the source documents (glossary: multiple concurrent Sessions; School License
diagram: one school-wide Session; Individual diagram: teacher-owned). It does
not resolve the conflict, but it establishes that the codebase already carries
a dual-ownership notion. **The code itself has not yet been read** —
`classrooms/models.py` is Phase 1 work, and this line is documentation, which
ranks below code in the authority order. Recorded here so the conflict is
opened with evidence rather than with the brief's hypothesis.

---

## 10. Constraints confirmed for the design phase

Not new findings — confirmed as still true, because the architecture document
must respect them.

**Confirmed** from [docs/ops/postgres-guard-rails.md](../../ops/postgres-guard-rails.md):

- pgbouncer runs **transaction pooling** in production. `LISTEN`/`NOTIFY` and
  session-level advisory locks **do not work**. Nothing uses them today, and
  **no Part 1 design may introduce them** — this rules out one common
  implementation of cross-worker coordination.
- `disable_server_side_cursors = True` is required, not optional.
- Connection-level Postgres configuration belongs on the role, never in
  `DATABASES["default"]["OPTIONS"]` — set there once, it took production down
  at connect time.
- **Client connection budget**: one web instance can hold up to 36 client
  connections; pgbouncer's `max_client_conn` defaults to 100. Part 1 adds
  background work; if it also adds web capacity, `max_client_conn` must rise
  first.
- Moving to psycopg3 would need `prepare_threshold=None`. Noted in case any
  Part 1 dependency forces the upgrade.

---

## 11. What blocks the rest of the workflow

| # | Blocker | Severity | Owner | Status |
|---|---|---|---|---|
| B-1 | Working tree shared with a concurrently-mutating session (F-1) | **High** | Owner | **Live.** Three further files were added by that session *during* the test run. Mitigated per-run by checksum verification; not resolved |
| B-2 | **93** uncommitted paths (was 89 at phase start), incl. untracked design docs and 6 migrations (F-3) | **High** | Owner | Open — and **growing during the engagement**. Decision requested in D2 |
| B-3 | Two Part 1 inputs are `.docx` (F-4) | Low | — | Resolvable by conversion in Phase 1 |
| B-4 | Local Postgres 18.6 vs CI/production 16 (§5) | Medium | — | Any local concurrency/locking evidence must be re-confirmed on 16 |
| B-5 | H-1 stage-2 progress figure is stale and moving (F-5) | Medium | Owner | Must be frozen before sequencing is committed |

**None of B-1…B-5 blocks Phase 1 discovery**, which is static analysis against
cited files. B-1 and B-2 do block *implementation*, and B-4 blocks quoting any
local performance or concurrency measurement as production evidence.

---

## 12. Phase 0 rule compliance

| Rule | Status |
|---|---|
| §7 — no application code, migration, settings, test or fixture modified | ✅ Held. Only new files under `docs/phase2/architecture/` were written |
| §7 — no commit, stash or rebase | ✅ Held. One owner-approved `git checkout beta` + `git worktree prune` |
| §7 — nothing run against production | ✅ `ENVIRONMENT=local`, confirmed before any command |
| §2.9 — no student personal data in this document | ✅ Held. No names, no submission text; counts and identifiers only |
| §2.3 — no fabricated results | ✅ Held. The test result is marked pending rather than predicted |
