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
