# Step 4 per-module pass at 8063c44 (2026-09-30)

The 48 step 4 modules, run one at a time on the step 4 worktree (8063c44, restacked on 55a0ee0),
each under `nice -n 10 timeout -k 60 600 python manage.py test <label> --settings=settings_worktree --noinput -v 2`
(`--keepdb` after the first), with `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200`.

`SUMMARY.txt` is the runner's own summary: all 48 rc=0, none timed out, 09:58 to 10:21.
The `.log` files are trimmed from the full `-v 2` logs to one outcome line per test (each file's count matches `ran=`) and the
`Ran` / `OK` lines, with email addresses redacted; the full logs stayed in the session scratchpad.

This pass, with identical Redis command counts with and without step 4, is what cleared step 4
of the 19-hour hang (the cause was stage 3's per-classmate fan-out; see
H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md, the rework section).
