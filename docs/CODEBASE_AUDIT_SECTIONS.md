# Codebase Audit Sections — Grade A+ Backend

This document divides the repository into sections so a full code-review +
test-audit pass (see `docs/CODE_REVIEW_STANDARDS.md` for the checklist, and
`docs/prompts/` for the prompts that drive each pass) can cover every part of
the codebase without skipping anything. Go through sections roughly in the
order listed — later sections depend on the models/services defined in
earlier ones.

Each section lists: what it covers, why it matters, and what "done" looks
like for that section's pass.

---

## 0. Cross-cutting (do first — everything else depends on this)

**Covers:** `AutoGrader/` (settings, urls, middleware, celery, dispatch,
health, pagination, error_messages, uploads, cache_utils, request_context),
`manage.py`, `Dockerfile`, `requirements.txt`, `pyproject.toml`,
`.pre-commit-config.yaml`, `.coveragerc`, `.env` / `.example.env`.

**Why first:** this is the shared foundation — settings, middleware, error
handling, and Celery wiring that every app builds on. Getting this wrong
invalidates conclusions drawn about individual apps.

**Done when:** pre-commit passes clean repo-wide; settings are fully
environment-driven; every app's `AutoGrader/tests_*.py` pass; the
`.coveragerc` source list gap (missing `grading`, `ocr_processor`) is
resolved or explicitly deferred with a reason.

## 1. `users` — identity, auth, permissions

**Covers:** `users/models.py`, `services.py`, `views.py`, `permissions.py`,
`serializers.py`, `throttling.py`, `middleware.py`, `renderers.py`,
`mailerlite_service.py`, migrations.

**Why:** every other app's tenancy/authorization model depends on how users,
roles (student/teacher/school admin/super admin), and email-domain rules are
enforced here. Per project memory, personal-email and business-email tracks
are a deliberate, unmerged separation — the review should confirm nothing
introduces a merge path.

**Done when:** authz checklist (§3 of standards doc) passes for every
view/viewset in `users/views.py`; throttling and activation-email domain
rules are covered by their existing tests
(`tests_activation_email_domain.py`, `tests_email_domain_rules.py`,
`tests_open_signup.py`, `tests_superadmin_tenancy.py`).

## 2. `billing` — credits, subscriptions, Stripe, refunds

**Covers:** `billing/models.py`, `services.py`, `stripe_service.py`,
`webhooks.py`, `license_service.py`, `subscription_resolver.py`,
`refunds.py`, `immutable.py`, `access_control.py`, `checks.py`,
`middleware.py`, `qa_console.py`, `qa_time_travel.py`, `live_qa/`,
`billing/tests/`.

**Why:** this is the highest-blast-radius app — real money, Stripe webhooks,
and the append-only ledger just hardened in `e2dc83b`. Errors here are
financial, not cosmetic.

**Done when:** every write path to `CreditLedger` / `CreditUsageLog` is
confirmed append-only; webhook signature verification and timeout sync
(`scripts/check_gunicorn_timeout_sync.py`) are re-verified, not assumed;
`billing/tests/` and `BILLING_SERVICE_REVIEW.md` findings are cross-checked
against current code (that doc may be stale — verify, don't just cite it).

## 3. `classrooms` — enrollment, roster management

**Covers:** `classrooms/models.py`, `services` (via `signals.py`,
`views.py`), `permissions.py`, `serializers.py`, `urls.py`.

**Why:** the tenancy boundary between schools/teachers/students is enforced
here; a leak lets one classroom see another's data.

**Done when:** every queryset in `classrooms/views.py` is scoped by
tenant; `test_bulk_enrollment.py` and `test_views.py` cover the bulk paths,
not just single-record CRUD.

## 4. `assignments` — assignment authoring, PDF pipeline

**Covers:** `assignments/models.py`, `services.py`, `pdf_cache.py`,
`pdf_document.py`, `pdf_renderer.py`, `prosemirror_converter.py`,
`rigor.py`, `schema.py`, `vendor/`.

**Why:** PDF rendering was just changed to load-shed and pre-render on
publish (`ee0a69f`) — confirm the cache/pre-render path is correct under
concurrent load, not just single-request tests. Per project memory, the
Tiptap frontend editor forces full re-extraction on any edit — confirm no
new code path assumes partial/incremental extraction is possible.

**Done when:** `tests_pdf_cache.py`, `tests_pdf_renderer.py`,
`tests_prerender.py` all pass and actually exercise the load-shedding
behavior described in the recent commit, not just the golden path.

## 5. `ai_processor` — extraction, grading, benchmarking

**Covers:** `ai_processor/services.py`, `objective_grading.py`,
`answer_completeness.py`, `evidence.py`, `second_opinion.py`,
`grading_cache.py`, `extraction_schemas.py`, `grading_schemas.py`,
`tools.py`, `benchmark/`, all `*_PROMPT*.txt` files.

**Why:** this is the core product logic and the largest, most test-heavy app
in the repo (30+ `tests_*.py` files). `ai_processor/services.py` has
uncommitted changes as of this audit's start — that file should be the
first thing reviewed in this section, not the last.

**Done when:** every prompt file referenced by `services.py` is confirmed
current (no dangling reference to a superseded `_PROMPT_2` when `_PROMPT_3`
exists); reproducibility/benchmark suites
(`tests_reproducibility_scoring.py`, `tests_grading_benchmark.py`,
`tests_extraction_benchmark*.py`) pass; per standing project rule, any
change to AI-facing code is verified with one real (non-mocked) API call,
not mocks alone.

## 6. `grading` — grading records

**Covers:** `grading/models.py`, `views.py`, `admin.py`.

**Why:** small app, easy to skip — don't. It's also missing from
`.coveragerc`'s `source` list, so coverage numbers for it may be misleading
until that's fixed (see §0).

**Done when:** `grading/tests.py` is confirmed non-trivial (not a stub);
coverage gap from §0 is resolved for this app specifically.

## 7. `students` — submissions, grading dispatch, task tracking

**Covers:** `students/models.py`, `services.py`, `tasks.py`,
`task_context.py`, `task_tracking.py`, `signals.py`,
`second_opinion_serializers.py`, `exceptions.py`.

**Why:** this app owns the Celery task orchestration for the grading
pipeline, including broker-outage recovery and idempotency — the highest
concurrency-risk logic outside billing.

**Done when:** `tests_broker_outage.py`, `tests_grading_idempotency.py`,
`tests_task_tracking.py`, `tests_second_opinion_queue.py` pass and are
confirmed to actually simulate failure/retry, not just the success path.

## 8. `dashboard` — reporting, risk, rigor scoring

**Covers:** `dashboard/models.py`, `services.py`, `risk.py`, `rigor.py`,
`throttling.py`, `at_risk_improvements.py`,
`AT_RISK_IMPLEMENTATION_GUIDE.py`.

**Why:** aggregation queries here are the most likely place for N+1s and
missing pagination (§7 of standards doc) since dashboards summarize across
many rows.

**Done when:** every list/aggregate endpoint in `dashboard/views.py` is
checked for query efficiency; `AT_RISK_IMPLEMENTATION_GUIDE.py` is confirmed
to be documentation-as-code that's still accurate, or flagged if stale.

## 9. `ocr_processor`

**Covers:** `ocr_processor/models.py`, `views.py`.

**Why:** small and thin today, but handles untrusted file input — security
checklist (§3, especially upload validation) applies in full even though
the app is small. Also missing from `.coveragerc` (see §0).

**Done when:** upload validation is confirmed explicit; coverage gap
resolved.

## 10. Templates, static assets, media

**Covers:** `templates/` (including `assignment_to_prosemirror.py`,
`json_converter.py` — code files living in a templates directory is itself
worth a boundary check against §1 of the standards doc), `static/`,
`media/`.

**Why:** `templates/` contains Python modules alongside HTML — confirm this
is intentional packaging, not misplaced code that belongs in an app.

**Done when:** boundary question above is resolved one way or the other and
recorded; no secrets or PII committed under `media/`.

## 11. Root-level repository hygiene

**Covers:** everything sitting loose at the repo root that isn't a
standard project file — duplicate/misnamed HTML test pages
(`google auth test.html`, `google-auth-test.html`, `google_auth_test.html`),
`requieremnt update`, `elf):`, loose PDFs and planning docs
(`Grade A+ Subscription model.pdf`, `Stripe Implementation.pdf`,
`URL Structure.pdf`, `SUBSCRIPTION_FLOW_DIAGRAMS.md`,
`SUBSCRIPTION_FLOW_PLAIN_LANGUAGE.md`, `IMPLEMENTATION_SUMMARY.md`,
`FINAL_VERIFICATION_REPORT.md`, `FUTURE_ROADMAP.md`, `GRADING_FLOW.md` /
`.pdf`, `GRADING_HANDBOOK.md`, `SPECIFICATION_V2.md`,
`API_DOCUMENTATION_LICENSE.md`, `API_LAYER_SUMMARY.md`, `assignent.html`,
`branching-system.png`, `AutoGrader flow.svg`, `stripe commands.txt`,
`QA_SERVER_SETUP.md`), sample data at root (`sample_student.pdf`,
`sample_teacher.pdf`, `Files/`), local/generated artifacts
(`celerybeat-schedule.*`, `.coverage`, `.mypy_cache/`, `.DS_Store`),
environment files (`.env`, `.example.env`, `live.env`, `QA.env`).

**Why:** a cluttered root makes it hard to tell what's load-bearing vs.
leftover, and increases the chance of accidentally shipping something like
`live.env`. This is the literal "removing unnecessary files and documents"
part of the task.

**Done when:** every file above has a documented disposition — keep in
place, move under `docs/`, or delete — proposed to the user for
confirmation (never deleted or moved unilaterally); `.gitignore` is updated
so generated artifacts stop reappearing; confirm none of the `.env`-pattern
files are tracked in git when they shouldn't be.

## 12. `docs/` itself

**Covers:** the existing `docs/` tree, including the large
`docs/backend/` reference set.

**Why:** documentation drifts from code silently. `docs/backend/*.md` files
were generated Aug 26 — confirm they still match the apps they describe
after this audit's findings, especially anywhere this audit found a gap.

**Done when:** any doc found to describe behavior that no longer matches
code is flagged (not silently rewritten — confirm the correct behavior
first).

---

## Task list (tracking template)

Use this table to track progress. Status values: `not started`,
`in progress`, `gaps found`, `clean`.

| # | Section | Status | Notes |
|---|---------|--------|-------|
| 0 | Cross-cutting (AutoGrader core, config, tooling) | not started | |
| 1 | users | not started | |
| 2 | billing | not started | |
| 3 | classrooms | not started | |
| 4 | assignments | not started | |
| 5 | ai_processor | not started | |
| 6 | grading | not started | |
| 7 | students | not started | |
| 8 | dashboard | not started | |
| 9 | ocr_processor | not started | |
| 10 | templates / static / media | not started | |
| 11 | root-level repo hygiene | not started | |
| 12 | docs/ | not started | |

This table is the thing to update as each pass of
`docs/prompts/CODE_REVIEW_PROMPT.md` and
`docs/prompts/TEST_AUDIT_PROMPT.md` completes for a section.
