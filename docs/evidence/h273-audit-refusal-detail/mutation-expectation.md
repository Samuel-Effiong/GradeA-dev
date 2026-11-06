# H-273 mutation battery: expectation (committed before the run)

Commit under test: 8f92f2b7 (code), battery script `mutants.py` beside this file.
Test set: audit.tests_h273_request_detail, audit.tests_h273_query_filters,
audit.tests_admin_action, audit.tests_state_change, AutoGrader.tests_middleware,
AutoGrader.tests_request_context, students.tests_batch_item_results.

27 mutants: NOTES §4 M-H273-1..23 (M-4 is the `_audit_exc` stash, since the
`_audit_exc_class` name in NOTES was never built; M-14 is split into 14 for
ADMIN_ACTION and 14b for STATE_CHANGE), M-24 = the school-admin scope on
target_name (named in tests_h273_query_filters.py's header but missing from
NOTES §4), and SM additions M-25/26 (reason code stash for enveloped refusals
and the generic Throttled).

Expected: every mutant applied (anchor found exactly once), every mutant
KILLED, every restore sha256 OK. Any survivor or anchor miss = STOP.
