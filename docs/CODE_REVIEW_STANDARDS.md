# Code Review Standards — Grade A+ Backend

This document lists the industry practices this codebase is expected to follow.
It is the checklist used by the code-review pass described in
`docs/CODEBASE_AUDIT_SECTIONS.md` and by the review prompt in
`docs/prompts/CODE_REVIEW_PROMPT.md`. Every section below maps to something a
reviewer (human or AI) should actually check — not aspirational language.

Where a practice is already enforced by tooling in this repo (pre-commit,
`.coveragerc`, etc.), that is noted so the review focuses on gaps, not on
re-litigating settled tooling choices.

---

## 1. Architecture & app boundaries

- Each Django app (`ai_processor`, `assignments`, `AutoGrader`, `billing`,
  `classrooms`, `dashboard`, `grading`, `ocr_processor`, `students`, `users`)
  owns one clear responsibility. A change that touches business logic in the
  wrong app is a boundary violation, not a convenience.
- Cross-app calls go through a `services.py` / public function, never by
  reaching into another app's models or private helpers directly.
- No circular imports between apps.
- Business logic lives in `services.py`, `models.py` methods, or dedicated
  modules — not in `views.py` or `serializers.py`. Views stay thin
  (parse request → call service → serialize response).
- Settings (`AutoGrader/settings.py`) stay environment-driven — no
  environment-specific literals hardcoded outside `.env` / `.example.env`.

## 2. Style & static analysis (already enforced — verify, don't re-argue)

This repo already runs `black`, `flake8` (with bugbear, comprehensions,
docstrings, import-order, print, eradicate), `isort`, `mypy`
(`--check-untyped-defs`), `bandit`, and `detect-secrets` via
`.pre-commit-config.yaml`. The review should:

- Confirm `pre-commit run --all-files` passes clean.
- Flag any `# noqa`, `# type: ignore`, or bandit suppression that isn't
  justified by an adjacent comment.
- Flag dead code that eradicate/flake8 would normally catch but that slipped
  through (e.g. commented-out blocks, unreachable branches).

## 3. Security

- **Secrets**: nothing hardcoded — checked against `.secrets.baseline`. Any
  new baseline entry needs a one-line justification in the PR, not a silent
  addition.
- **AuthN/AuthZ**: every view/viewset declares explicit permission classes;
  no endpoint relies on "nobody will guess this URL." Multi-tenant boundaries
  (school/classroom/teacher/student scoping) are enforced at the queryset
  level, not just the serializer level.
- **Injection**: no raw SQL string interpolation; use the ORM or parameterized
  `.raw()` / `cursor.execute()` calls only.
- **Mass assignment**: serializers declare explicit `fields`, not
  `fields = "__all__"` on writable serializers touching sensitive models
  (billing, users, credits).
- **File uploads / OCR / PDF pipeline**: validate content type and size
  before processing; never trust a client-supplied filename or MIME type
  for control flow.
- **Webhooks** (Stripe): signature verification happens before any state
  change; timeouts match the documented gunicorn/webhook contract
  (`scripts/check_gunicorn_timeout_sync.py` already guards this — don't
  reintroduce drift).
- Run `bandit -c pyproject.toml` and treat any new finding as a blocker, not
  a suppression target.

## 4. Data integrity & migrations

- Migrations are reversible where practical, and never mix schema changes
  with large data backfills in one migration (see `docs/MIGRATIONS.md` for
  the existing convention).
- Financial/audit models (`CreditLedger`, `CreditUsageLog`, and anything
  under `billing/immutable.py`) stay append-only — no `update()` /
  `save()` path that mutates a historical row. This was just hardened
  (`e2dc83b`); the review should confirm no new code path reopens that gap.
- Foreign keys specify an explicit `on_delete`; no accidental `CASCADE` on
  models where an audit trail must survive the parent's deletion.
- `docs/MIGRATIONS.md` and `scripts/check_migration_safety.py` are the
  source of truth for what "safe" means here — apply that standard, don't
  invent a new one.

## 5. API design (Django REST Framework)

- Consistent error response shape across all endpoints (check
  `AutoGrader/error_messages.py` / `handlers.py` for the existing
  convention and flag any endpoint that diverges).
- Pagination applied to every list endpoint that can return unbounded rows
  (`AutoGrader/pagination.py`).
- Serializers validate input shape and business rules that belong at the
  API boundary (not duplicated business logic that already lives in a
  service).
- URL patterns are RESTful and consistent with sibling apps' `urls.py`.

## 6. Async / Celery tasks

- Every task is idempotent — safe to retry after a broker outage
  (`students/tests_broker_outage.py` already tests this pattern; new tasks
  should meet the same bar).
- Tasks that touch billing or grading state use the existing idempotency /
  task-tracking helpers (`students/task_tracking.py`,
  `students/task_context.py`) rather than inventing new locking.
- No task silently swallows an exception — failures are logged with enough
  context to debug from Celery logs alone.

## 7. Performance & scalability

- No N+1 queries on list endpoints — `select_related` / `prefetch_related`
  used wherever a serializer walks a relation.
- Expensive AI/LLM calls and PDF renders are cached or pre-rendered where a
  cache already exists (`ai_processor/grading_cache.py`,
  `assignments/pdf_cache.py`) — don't bypass the cache path.
- Database indexes exist on columns used in frequent `filter()` /
  `order_by()` calls, especially tenant-scoping columns.
- Nothing in a request/response cycle does synchronous work that belongs in
  a Celery task (AI grading calls, PDF generation, email sends).

## 8. AI / LLM pipeline specific

- Prompts (`ai_processor/*_PROMPT*.txt`) are versioned by suffix
  (`_2`, `_3`, …) rather than edited in place — confirm the code references
  the intended latest version and that superseded prompt files are either
  archived or clearly marked deprecated, not left ambiguous.
- Grading/extraction changes ship with a benchmark or reproducibility check
  (`ai_processor/benchmark/`, `tests_reproducibility_scoring.py`,
  `tests_grading_benchmark.py`) — a change to grading logic without a
  benchmark run is incomplete, per this repo's own recent history
  (`b6523ed`).
- Structured-output schemas (`grading_schemas.py`, `extraction_schemas.py`)
  are the contract with the model — a prompt change that silently drifts
  from its schema is a defect, not a style issue.
- Per project memory: AI-prompt or billed-API changes must be verified with
  a real call, not just a mock — the review prompt should call this out
  explicitly rather than accepting mocked-only test coverage as proof.

## 9. Testing standards

- Every app follows the existing `tests.py` / `tests_<topic>.py` naming
  convention (Django + this repo's own pattern — see
  `name-tests-test` exclusion in `.pre-commit-config.yaml`).
- New behavior ships with tests that pin it down hard enough to catch a
  regression, not just exercise the happy path once.
- Coverage is measured against `.coveragerc`'s existing `source` list
  (`ai_processor`, `assignments`, `AutoGrader`, `billing`, `classrooms`,
  `dashboard`, `students`, `users`) — note `grading` and `ocr_processor`
  are NOT in that source list; flag this as a gap to resolve, not something
  to silently work around.

## 10. Observability & error handling

- Exceptions that reach the user return the standard error envelope
  (`AutoGrader/error_messages.py`), never a raw stack trace or Django debug
  page in production paths.
- Structured logging includes enough context (user/school/task id) to trace
  an incident without re-running the request.
- Health checks (`AutoGrader/health.py`, `beat_health.py`) reflect real
  dependency status (DB, Redis/broker, not just "process is alive").

## 11. Dependency & config hygiene

- `requirements.txt` has no unused or duplicate entries; anything pinned
  loosely (`>=`) is a deliberate choice, not an oversight.
- `.env` / `.example.env` stay in sync — every setting read in
  `AutoGrader/settings.py` has a documented example entry.
- No secrets or environment-specific values committed outside `.env`-style
  files (`live.env`, `QA.env` should be confirmed as gitignored, not
  tracked).

## 12. Repository cleanliness

- No stray root-level scratch files masquerading as project files — this
  repo currently has several worth resolving during cleanup (not
  necessarily deleting without confirmation): duplicate HTML test pages
  (`google auth test.html`, `google-auth-test.html`,
  `google_auth_test.html`), a misspelled file (`requieremnt update`), an
  oddly named artifact (`elf):`), loose PDFs/spec docs at repo root
  (`Grade A+ Subscription model.pdf`, `Stripe Implementation.pdf`,
  `SUBSCRIPTION_FLOW_DIAGRAMS.md`, etc.) that likely belong under `docs/`.
- Generated/local artifacts (`celerybeat-schedule.*`, `.coverage`,
  `.mypy_cache`, `htmlcov/`) should be gitignored, not committed.
- Any file removal or move is a call-out for the user to confirm, not a
  silent deletion — see `docs/prompts/CODE_REVIEW_PROMPT.md` for how the
  review should handle this.

## 13. Documentation

- Code comments explain *why*, not *what* — matches this project's existing
  style; flag comment blocks that just restate the code.
- Any new architectural decision that isn't obvious from reading the code
  gets a short doc under `docs/` (this repo already does this well — see
  `docs/backend/`, `docs/ops/`).

---

## How this document is used

`docs/CODEBASE_AUDIT_SECTIONS.md` divides the repo into review sections.
For each section, apply every relevant checklist item above and record:
**pass / gap found / not applicable**, with a file:line reference for each
gap. Aggregate results feed the task list tracked at the top level of the
audit.
