# H-14 RESUME (shutdown checkpoint, 2026-09-27)

## Current step
Fix and equivalence tests are committed and passing. The mutation battery
(`02_mutation_battery.log`) was IN PROGRESS when the shutdown order arrived:
mutants a-d showed `RESTORE_MISMATCH` in the log, which is a real problem in
`mutants.sh`'s restore step (or a race with a concurrent process), not
evidence about the fix. The file was mid-mutant-f (blank stand-in for the
due_date filter) when I killed the process; `git checkout -- dashboard/services.py`
was run afterward and verified back to the committed sha256, so the tree is
clean.

## What's actually verified
- Fix commit on this branch (`git log -1`): scalar-column rewrite of
  `_at_risk_students`, `dashboard/services.py` + `dashboard/tests_h14_at_risk_equivalence.py`.
- 4/4 equivalence tests pass (oracle = the original body verbatim from beta
  4b902fc): same students, scores, order, get_full_name, zero extra queries
  per returned student, one net extra query overall vs. the original, flat
  under 20 extra students.
- Payload byte-identical before/after on the seeded s3-scale dataset
  (10 schools x 240 courses x 6,000 students): docs/evidence/h14_school_admin_summary
  /01_before_profile.json vs /01_after_profile.json (also saved from scratchpad).
- Cold rebuild: before p50 ~4,405 ms (17 queries), after p50 ~2,059 ms
  (18 queries; +1 from the students_by_id in_bulk lookup). Measured on a
  loaded machine (load ~5-9), not a quiet one - direction is solid, absolute
  figures are not final.
- Targeted regression: `dashboard` app + the 4 cache-freshness modules
  (AutoGrader.tests_cache_user_fanout/_dashboard_wide/_invalidation_coverage/
  _dashboard_2329) = 311 tests OK (docs/evidence/h14_school_admin_summary/
  03_targeted_dashboard_cache.log).

## Not done / exact next command
1. Fix `mutants.sh`'s restore step (find out why checkout didn't match
   sha256 for mutants a-d - check if `git checkout --` was actually run in
   this worktree's cwd, or whether another process wrote to the same file
   concurrently) and rerun clean:
   `bash docs/evidence/h14_school_admin_summary/mutants.sh 2>&1 | tee docs/evidence/h14_school_admin_summary/02_mutation_battery.log`
2. Write `docs/evidence/H14_SCHOOL_ADMIN_SUMMARY_EVIDENCE.md` with the gate
   table once the mutation log is clean.
3. Hand to Verification Engineer.

## Seed / harness state (for re-measuring, not committed - lives outside the repo)
Dedicated DB `ag_h1_stampede_h14`, dedicated Redis on 127.0.0.1:6391 (both
still running as of the checkpoint), harness copied into the session
scratchpad from docs/evidence/h1_stampede/harness.tar.gz. Profiler:
`<scratchpad>/profile_summary.py <label> <out-prefix>`. Env needed:
`source <scratchpad>/stampede_db.env; export STAMPEDE_REDIS=redis://127.0.0.1:6391/0 PYTHONPATH=<scratchpad>:$PWD DJANGO_SETTINGS_MODULE=stampede_settings`.
