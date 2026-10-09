# H-194 (AUDIT-ANON-FLOOD): an anonymous probe of the super-admin routes cannot write unbounded audit rows

Branch `task/audit-anon-flood-cap`, base phase2/epic-a 75d826202249 (next-stage line, local; not pushed). Written by ed (Security Engineer), 2026-10-08. Severity LOW-MEDIUM (Senior Manager). **Nothing has been run on this branch yet.**
Marks: READ = read in the code; NOT RUN = reasoning; UNVERIFIED = not checked against the deployment.

## The fault (READ; NOT RUN)

An unauthenticated request to any `IsSuperAdmin` route is refused, and the refusal is audited: `IsSuperAdmin.has_permission` calls `emit_denied` (audit/admin_action.py), which writes one ANONYMOUS `ADMIN_ACTION` / `DENIED` row per call. The failed-sign-in cap (`audit/failed_auth_cap.py`)
applied only to `AUTH_LOGIN` and `ACCOUNT_REGISTER` (`_CAPPED_ACTIONS`, audit/emitter.py), so these rows were bounded only by the anonymous throttle (60 a minute per client key, settings). At the cap one client writes 86,400 rows a day; the audit volume harness uses about 485 bytes a row, index
ratio 1.75 (audit/volume.py), so about 0.85 KB with indexes: about 73 MB a day and about 27 GB over the 365 days a GENERAL row is kept (audit/tasks.py: rows deleted after 365 days; IP and user agent blanked after 90). More clients multiply it (the throttle is per client, not global).
**THE 60-A-MINUTE BOUND IS UNVERIFIED IN DEPLOYMENT.** `NUM_PROXIES` (settings) defaults to 2 in code while its own comment says 1 is a single edge hop; with a real chain of one hop and a value of 2, DRF reads an address the client can write, and every request would be its own client, so the throttle would not bound anything. The deployed value has not been checked against the real number of proxy hops (the Senior Manager asked the user; nobody on the team looks at a settings value). Until it is, this cap is the only bound that does not depend on it.

## The change (READ; NOT RUN), `audit/emitter.py` only

`_failed_auth_cap_scope`: an anonymous `ADMIN_ACTION` / `DENIED` goes into the GLOBAL, no-target bucket of the existing failed-auth cap from the first event (`return None, True`), with the existing summary row (at 1, 10, 100 suppressed ...). The route's target is a URL id the caller chooses, so it is IGNORED: a per-target bucket would give every id its own floor.
A signed-in refusal and a granted action never reach this point (the function returns early for a non-anonymous requester and for non-DENIED outcomes elsewhere). No migration, no new setting, no new metric (the existing `audit_failed_auth_suppressed_total` counts suppressed events).

## KNOWN LIMIT, in plain words

The global bucket (300 an hour by default) is the one failed sign-ins of UNKNOWN addresses also use. An admin-route flood can use it up, so failed sign-in rows for unknown addresses are suppressed for the rest of that hour (their summary row says so); a known account's first five failures an hour are still always written. A separate bucket for admin refusals is a follow-up (a new setting and a key), not built.

## Tests (written first, b427ed44; NOT RUN)

`audit/tests_anonymous_admin_denial_cap.py`, small limits (global 6): the route refuses an anonymous caller (a guard that the probe reaches the refusal); probes stop writing rows at the global limit (rows == 6 of 10); the first suppressed probe leaves exactly one summary row (cap `global`, limit 6, anonymous); naming a different target each time does not get round the cap; a signed-in refusal is never capped.

## Written expectations, before any run

- **Step 0 (reproduce-first)**: `audit/emitter.py` as at 75d82620 under the new module: **Ran 5, THREE red**: `test_anonymous_probes_stop_writing_rows_at_the_global_limit`, `test_the_first_suppressed_probe_leaves_one_summary_row`, `test_naming_a_different_target_each_time_does_not_get_round_the_cap`. Green on the old code, by design: `test_the_route_exists_and_refuses_an_anonymous_caller` (a guard no mutant isolates) and `test_a_signed_in_refusal_is_never_capped` (seen red by mutant F4).
- **Step 1**: `makemigrations --check` no changes; the new module (Ran 5, OK), `audit.tests_failed_auth_cap` and the other audit modules named in the script, with the guard modules that exist on the next-stage line: OK, no FAIL or ERROR line.
- **Step 2, mutants (4)**: F1 (not capped): the limit, summary and different-target tests; F2 (the route target is the cap key): the different-target test only; F3 (the global cap does not apply): the limit, summary and different-target tests; F4 (a signed-in refusal is capped too, the anonymous guard removed): the signed-in test and the existing `test_a_signed_in_requesters_failures_are_never_capped` of `tests_failed_auth_cap`.
- **Step 3**: the audit app, one serial run: OK.
- Nothing is re-run without the Release Engineer's word.

## Not shown by any run so far

Everything: nothing has been run. And, even when run: the deployed throttle value, how many rows a real flood writes, the size of a row in the deployed table.

## Taken over and re-traced by ed, 2026-10-09 19:5x WAT (nothing run yet): ONE FAULT FOUND IN THE BRANCH'S OWN FIX
- **Base update:** merged phase2/epic-a d2ad0405 (clean; audit/emitter.py, failed_auth_cap.py and admin_action.py have no difference between 75d82620 and d2ad0405).
- **Hand trace of `test_the_first_suppressed_probe_leaves_one_summary_row` against the real code:** the summary is written by `_emit_failed_auth_summary` as an `ADMIN_ACTION`/`DENIED` row with metadata `cap`, `suppressed_so_far`, `limit`, `window_seconds`; but `METADATA_ALLOWLIST[ADMIN_ACTION]` was `{"source"}`, so `sanitise_metadata_for_action` DROPS all four keys (audit/metadata.py `sanitise_metadata_for_action`: a key not in the action's list is narrowed away). The row would be written with empty metadata and the test's `rows[0].metadata["cap"]` would fail with a KeyError on the fix. It would have been a wrong-reason red had it been run as is. **Change:** `ADMIN_ACTION` now allows `{"source"} | _FAILED_AUTH_SUMMARY_KEYS` (the same device as AUTH_LOGIN, STATE_CHANGE and ACCOUNT_REGISTER); `audit/metadata.py`, one line plus a comment. No test pins the ADMIN_ACTION list exactly (grep of every user of `METADATA_ALLOWLIST`/`metadata_allowlist_for`: tests_metadata, tests_emitter, tests_history, students/tests_grading_label_written; none asserts ADMIN_ACTION's set; tests_history needs only `{"changed_fields","source"} <= ` for PERMISSION_CHANGE).
- **New mutant F5** (widening removed, in audit/metadata.py): expected failing = G2 only (the summary row is written but `metadata["cap"]` is missing: written fragment `KeyError: 'cap'`). Mutate.py `--check`: 5 mutants, anchors unique.
- **Written reasons (rule 22), step 0 on the old emitter.py AND old metadata.py:** G1 `LIMIT`: `10 != 6`; G2: `0 != 1` (no summary row: nothing is suppressed on the old code); G3: `10 != 6`. G0 and G4 green by design. Each count is a deciding value (G0 proves the probe reaches the refusal with exactly one row; G4 proves signed-in rows are 10 of 10).
- **Old behaviour grep:** tests that count anonymous ADMIN_ACTION DENIED rows above the default global limit (300): none found by grep of `emit_denied` / `IsSuperAdmin` anonymous tests (they write a handful of rows); `audit.tests_failed_auth_cap` shares the global bucket but resets the cache in setUp; the gate list below includes it and `audit.tests_admin_action`, `audit.tests_metadata`, `audit.tests_emitter`, `audit.tests_history`.
- **What the bound is, plainly:** the cap allows at most 300 anonymous admin-route refusals an hour to be written (all clients together), then one summary row at the 1st, 10th, 100th ... suppressed; before the fix one client could write up to its throttle's 60 an hour per minute, i.e. about 86,400 rows a day (about 27 GB a year with indexes), and several clients more. After the fix: at most about 300 rows an hour (7,200 a day, twice that across a window edge) plus a few summaries, whatever the throttle does. "UNVERIFIED in deployment" means: nobody has checked the deployed proxy count (`NUM_PROXIES`, code default 2); if it is wrong the throttle sees every request as a new client and does not slow the attacker at all. The fix no longer depends on that, because its bound is global. What an attacker can STILL do after the fix: (1) use up the shared 300-an-hour global bucket so that failed sign-ins of unknown addresses are not written for the rest of that hour (their summary row says so; a known account's first five failures an hour are still always written); (2) hide the admin-route probe rows of a real attack behind the cap (the metric `audit_failed_auth_suppressed_total` still counts them); (3) keep making the requests, which cost CPU and database reads but no audit rows beyond the cap. A separate bucket for admin refusals would remove (1); not built.
- **Gate list for the slot:** step 0 (G0-G4), makemigrations --check, `audit.tests_anonymous_admin_denial_cap`, `audit.tests_failed_auth_cap`, `audit.tests_admin_action`, `audit.tests_metadata`, `audit.tests_emitter`, `audit.tests_history`, `audit.tests_query_api`, the repo-wide guard modules; mutants F1-F5; then the audit app plus users as the regression.

## Verifier 1's pre-read (2026-10-09), applied
No fault found. Traced with limit 6 and 10 attempts: counts 1-6 written, count 7 the first suppression (`_is_threshold(1)`), exactly one summary; 8-10 no more. G1 6 rows, G2 one summary, G3 6 rows. The summary is written with the bypass, so it does not recurse.
- **F2's set `[G3]` holds only because** the list route has no pk and no `get_audit_target`, so `target_id` is None through the route (G1, G2 cannot see a per-target key); G3 emits with a different `target_id` each time, which is what F2 changes.
- **F4:** G4 goes red with `6 != 10`; the existing `audit.tests_failed_auth_cap` test `test_a_signed_in_requesters_failures_are_never_capped` is also expected red and stays listed.

## RESULTS (step 1 on f5eedce0, 2026-10-09 20:11:26-20:14:51 WAT, Release Engineer's grant 20:11, one systemd-inhibit; raw files beside this one)
- **Step 0 (base audit/emitter.py and audit/metadata.py under the new module):** Ran 5, FAILED (failures=3): exactly the three written tests, each with its written reason (runner step (r): "reason ok" three times: `10 != 6`, `0 != 1`, `10 != 6`). G0 and G4 green, as written. `prefix_base_production_failing.txt`.
- **makemigrations --check:** exit 0, "No changes detected".
- **New + related modules + repo-wide guards (`modules_and_guards.txt`):** Ran 507 in 121.3 s, OK; load 3.37 at start, 2.95 at end.
- **Mutants (`mutation_log.txt`, `mutation_results.json`, `mutant_logs/`): 5 of 5 KILLED, no survivor, none broken, "source clean after mutants", every set exactly as written, no extras and no misses.** F1 failed 3 (G1, G2, G3); F2 failed 1 (G3); F3 failed 3 (G1, G2, G3); F4 failed 2 (G4 with `6 != 10`, and the existing `audit.tests_failed_auth_cap` signed-in test); F5 ERRORED 1 (G2, with the written fragment `KeyError: 'cap'`).
- Regression of the audit app and users: a later grant (step 3).

## Verifier 1's record and the regression for H-194

Verifier 1's record for H-192 and H-194 is committed byte for byte beside this file as `VERIFICATION_h192_h194.md` (sha256 begins ffb1fd5488df3e29). It covers tip f5eedce0 (gate record 811318a0).

Regression: the promotion's one full run of the merged tip is the regression for this row (SM, rule 15). No separate audit and users run is made for it.
