# Verification: H-192 (tip 3dd1aebe) and H-194 (tip f5eedce0)

**Verifier:** 1a. **Asked by:** the Senior Manager (19:33), slot granted by 0b. **Date:** 2026-10-09, clock read from `date`. Base for "old": d2ad0405 production files. Scratch worktrees `vf_h192` and `vf_h194` (detached at the tips), own test databases. Probes go through the real routes; the probe modules only were run (no regression repeated, rule 15). Probes, runner and mutant file: `h192_probe_tests_vf1a_h192.py`, `h194_probe_tests_vf1a_h194.py`, `h192_h194_run.sh`, `h192_h194_mut.py`; expected results written BEFORE any run in `H192_H194_probe_expectations.md` (corrections appended before each re-run). Logs: `runs/h192_3dd1aebe/`, `runs/h194_f5eedce0/`.

## Verdict: H-192 VERIFIED. H-194 VERIFIED-WITH-NOTES.

## H-192: a school admin with no school sees nothing
Run 20:39:38 to 20:40:41 (load 3.4), as written:
- old: Ran 5, 2 red, p1 and p2, fragment `Lists differ ... != []` (the school-less admin saw the school-less rows).
- fix: Ran 5, OK. p0 (a REAL failed sign-in, posted to the login route from 203.0.113.77, wrote a school-less AUTH_LOGIN row) and p4 (the super admin still sees it) are green, so the fixture does hold a row to leak.
- M1 (`if False`): Ran 5, 2 red (p1, p2). M3 (`if True`): Ran 5, 1 red (p3, an admin WITH a school loses their own rows).
- Covers: no school sees nothing, also with `action` and another school's `school_id` filters; an admin with a school sees only own; the super admin unchanged.

## H-194: the anonymous denial cap
Run 20:43:56 to 20:45:15 (load 1.0), as written:
- old: Ran 3, 1 red, q1 `10 != 6` (ten anonymous denials, ten rows). fix: Ran 3, OK (6 rows, 1 summary row with `cap == "global"`).
- F1 (cap bypass restored): q1 `10 != 6`. F4 (signed-in denial also put in the global bucket): q3 red `0 != 3`, q2 green. F5 (summary keys dropped in metadata.py): q1 ERROR `KeyError: 'cap'`.
- q2 and q3: a signed-in teacher's 3 denials are recorded 3 of 3, before the flood and after the flood used the cap up.

## Notes
- N1. Probe faults of mine, each a first stop, no product code touched, fixed before the re-run: (a) H-192 old run 20:25:10, my marker was the typed address but the row stores only `source_ip` (so p0 and p4 were red); (b) H-194 old/fix run 20:41:18, `5 != 6` in both, because only `IsSuperAdmin` calls `emit_denied` (classrooms/permissions.py:93): the school-admin route writes no denial row, so my second flood wrote nothing.
- N2. Known limit, ed's: the global bucket (default 300 an hour) is shared with failed sign-ins of unknown addresses, so a flood of anonymous admin probes can use it up and later unknown-address sign-ins are summarised instead of listed. Signed-in denials are not affected (q3).
- N3. The anonymous throttle bound (60 a minute per client) is UNVERIFIED in deployment (`NUM_PROXIES`); the cap does not depend on it.
- N4. H-193 (a SCHOOL_ADMIN must have a school) is a separate row and not started; H-192 only makes the school-less case empty.
- N5. I read ed's file lists, not his raw logs; his regressions (479 and 507 OK) are his, not repeated (rule 15). The audit+users regression of the merged tip is 0b's.
- N6. Rule 21: done with `vf_h192` and `vf_h194`; databases to drop: test_vf_h192, test_vf_h192_mut, test_vf_h194, test_vf_h194_mut. My untracked probe copies are deleted.
