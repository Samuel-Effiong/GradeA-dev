# H-122: two Epic A documents still described the old Sentry scrubber

Author: ed (Security), 2026-10-05. Branch `task/h122-old-scrubber-docs`, off `phase2/epic-a`
806c227e. LOW, documents only. From note 2 of v2's record on the bundle 7 merge-down
(`docs/evidence/epic-a-merge-down-b7/VERIFICATION_epic_a_merge_down_b7_318895d0.md`).

## What was stale

The bundle 7 merge-down replaced the epic's single `before_send` function with beta's H-89
module. Two places still described the old arrangement:

- `docs/phase2/architecture/04_epic_a_implementation_plan.md`, §0.5a items 2 and 3 (a
  grep-based lint rule; one `before_send` hook).
- The docstring of `scripts/check_no_pii_in_logs.py` (a baseline of grandfathered files).

Neither names the old function `scrub_pii_before_send`. The only file on the epic that does,
outside the merge-down's own evidence, is `docs/evidence/epic-a-pii-cleanup/EVIDENCE.md`, a
record of its time; it is left as written.

## The change

Additions only; no planned text is rewritten or removed.

- Plan 04: a dated "As built" note under item 3, saying what items 2 and 3 are today and where
  the evidence is. The FR-A-04 row of the test mapping is left as planned; the note says so.
- The checker: one dated paragraph at the end of its docstring. No code line changes.

## Checked

Each statement in the two notes was read against the tree at 806c227e:

- `scrub_event`, `scrub_breadcrumb`, `scrub_log` are defined in `AutoGrader/sentry_scrubbing.py`,
  and `AutoGrader/settings.py` passes them as `before_send`, `before_send_transaction`,
  `before_breadcrumb` and `before_send_log`.
- `LOG_SCRUB_ADDRESSES = not _TESTS_ARE_RUNNING` in `AutoGrader/settings.py`.
- `scripts/pii_log_baseline.txt` has no line that is not a comment.
- `AutoGrader/tests_no_pii_in_logs.py` exists; 2919e5aa is the merge of the bundle 7 merge-down.
- The three evidence folders the note names exist.
- No Python file reads either text. Five files outside `docs/` name the plan or the checker
  (grep): `audit/views.py`, `audit/tests_query_api.py`,
  `billing/tests/test_credit_transaction_audit.py` and `AutoGrader/tests_no_pii_in_logs.py`
  name them in a docstring or comment only. `scripts/test_check_no_pii_in_logs.py`, the
  checker's own self-test, imports the checker and calls `find_violations()`; it does not read
  the docstring.

The checker still parses and still passes on the whole repository after the docstring change
(`python scripts/check_no_pii_in_logs.py`: "OK: no new PII-in-logs violations."). That is the
static script, the same one the pre-commit hook runs; it is not a test run.

## Not done

No test run and no mutants: no executable line changed. The checker's self-test
(`python scripts/test_check_no_pii_in_logs.py`, no database) was NOT run: it is a test run and
needs 0b's grant like any other. It is the one run worth asking for if the verifier wants one.
