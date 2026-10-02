# Prompt 2 — Section Test Audit & Regression Lock-Down

Copy this prompt, fill in `<SECTION>` with one row from the task list in
`docs/CODEBASE_AUDIT_SECTIONS.md`, and run it. Run this AFTER Prompt 1 has
completed for the same section, so the tests are verifying reviewed code,
not code that's about to change again.

---

You are verifying that one section of the Grade A+ backend (a Django/DRF
app that auto-grades student assignments with an LLM pipeline, Stripe
billing, and Celery async processing) is backed by tests comprehensive
enough to make a regression impossible to ship silently.

Read first:

- `docs/CODE_REVIEW_STANDARDS.md`, section 9 (Testing standards) and
  section 8 (AI/LLM pipeline specific) — the bar this section's tests must
  clear.
- `docs/CODEBASE_AUDIT_SECTIONS.md` — find your assigned section and its
  "Done when" criteria.

Your assigned section for this pass is: **<SECTION>**

**Before running anything**, set up an isolated test database so this run
doesn't collide with other concurrent sessions on the same machine: create
a settings shim in your scratchpad directory that overrides
`DATABASES["default"]["TEST"]["NAME"]` to a name unique to this session
(include part of your session id — not a generic name), and run tests via
that shim with `--keepdb`. The first run against a new DB name pays full
migration cost (can take several minutes — let it run, don't assume a hang
and kill it early).

Do the following, in order:

1. **Enumerate every existing test file** for this section (the
   `tests.py` / `tests_<topic>.py` files listed or implied by
   `docs/CODEBASE_AUDIT_SECTIONS.md` for this section). Read them, not just
   their names — confirm what each one actually asserts, not what its name
   implies.
2. **Run the full test suite for this section** with coverage:
   `python manage.py test <app> --keepdb --parallel 1` under the isolated
   settings shim, plus `coverage run --source=<app> manage.py test <app>
   --keepdb` to get real numbers against `.coveragerc`'s scope. Report
   actual pass/fail counts and the coverage percentage — do not estimate.
3. **For every gap found by Prompt 1's review pass on this section**,
   confirm whether an existing test already covers that code path. If not,
   write one that pins the correct behavior down hard — asserting the
   actual output value, not just "no exception was raised." A test that
   would still pass after the bug was reintroduced is not a regression
   test.
4. **Check for untested failure modes**, not just untested happy paths:
   - Concurrent/retry behavior for anything Celery-backed (broker outage,
     duplicate task dispatch, idempotency).
   - Tenant-boundary violations (one school/classroom/user attempting to
     read or write another's data) for anything with a queryset.
   - Malformed or adversarial input at API boundaries (oversized upload,
     wrong content-type, missing required field) for anything user-facing.
   - For `ai_processor` / grading-adjacent sections specifically: a
     reproducibility or benchmark run (`ai_processor/benchmark/`,
     `tests_reproducibility_scoring.py`) — not just unit tests against
     mocked model output. Per this project's standing rule, any AI/LLM or
     billed-API code path must also be verified against a real API call
     at least once during this pass; a fully-mocked suite alone is not
     sufficient evidence the section works. Say explicitly whether you
     made that real call and what it returned.
5. **Add missing tests directly** for anything section 4 above turns up.
   Follow this repo's existing naming convention (`tests_<topic>.py`) and
   put them in the same app, alongside the code they test.
6. **Re-run the full suite** (this section plus any sibling apps it
   imports from or is imported by) after adding tests, to confirm nothing
   you added is flaky and nothing existing regressed:
   `python manage.py test <app> <related-apps> --keepdb --parallel 1`.
7. **Update the task list table** in `docs/CODEBASE_AUDIT_SECTIONS.md` for
   this section with the final coverage percentage and pass/fail counts in
   the notes column.

Do not report a section as test-complete if:
- Coverage on any file in scope is materially below the rest of the
  section without a stated reason.
- Any test only asserts "it didn't crash" for logic that has a real
  expected output.
- An AI/LLM code path in scope was verified only against mocks.

At the end, give a plain-language summary: current pass/fail counts,
coverage numbers, what regression protection you added and why it's hard
to accidentally break, and anything you could not verify (e.g. a real API
call you couldn't make, a load scenario you couldn't simulate) so the user
knows it's an open risk rather than assuming it's covered.
