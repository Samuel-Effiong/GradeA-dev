# H-195: the one unexplained 139-second hang, not seen again

Builder 2 (4d), 2026-10-25, read-only. The row (`docs/HARDENING_BACKLOG.md`, long
form in `GAP-pending-backlog-rows.md` H-195) stays open "until a hang is explained
or the series stay green over several CI runs". The hang was never explained; the
series stayed green. Closed on the Senior Manager's ruling of 2026-10-25.

## What was counted, and how

Each full-run log committed under `docs/evidence/beta-batch-NN/` was read in place
(the `.xz` is the raw log the evidence record names). For each: the number of
test lines of `assignments.tests_sanitizer_threads` (the module that holds the
cured text cleaner's thread tests and, at lines 116–121, the thread-stack dump
plus the `("hung", n)` marker when threads are alive at `HUNG_AFTER_SECONDS = 120`,
`THREADS = 8`), the number of `'hung'` markers, and the run's own `Ran` line.

```sh
cd <repo>
for f in docs/evidence/beta-batch-1[4-9]/strict_b*.log.xz docs/evidence/beta-batch-2[0-6]/strict_b*.log.xz; do
  printf "%s tests=%s hung=%s %s\n" "$(basename $f)" \
    "$(xzgrep -c '(assignments.tests_sanitizer_threads.' $f)" \
    "$(xzgrep -c "'hung'" $f)" \
    "$(xzgrep -h '^Ran [0-9]* tests' $f | head -1)"
done
```

Output on 2026-10-25 (worktree at b4af529f):

| batch | log | module's test lines | `'hung'` | Ran line |
|---|---|---|---|---|
| 14 | strict_b14.log.xz | 5 | 0 | Ran 6356 tests in 531.259s |
| 15 | strict_b15.log.xz | 5 | 0 | Ran 6455 tests in 428.744s |
| 16 | strict_b16.log.xz | 5 | 0 | Ran 6521 tests in 531.460s |
| 17 | strict_b17.log.xz | 5 | 0 | Ran 7647 tests in 549.063s |
| 18 | strict_b18.log.xz | 5 | 0 | Ran 7673 tests in 571.208s |
| 19 | strict_b19.log.xz | 5 | 0 | Ran 7692 tests in 843.345s |
| 20 | strict_b20.log.xz | 5 | 0 | Ran 7846 tests in 498.107s |
| 21 | strict_b21.log.xz | 5 | 0 | Ran 7876 tests in 601.843s |
| 22 | strict_b22.log.xz | 5 | 0 | Ran 8266 tests in 607.534s |
| 23 | strict_b23.log.xz | 5 | 0 | Ran 8286 tests in 562.207s |
| 24 (run 2) | strict_b24r2.log.xz | 5 | 0 | Ran 8311 tests in 556.834s |
| 25 | strict_b25.log.xz | 5 | 0 | Ran 8323 tests in 650.424s |
| 26 | strict_b26.log.xz | 5 | 0 | Ran 8348 tests in 922.118s |

Thirteen laptop full runs, 65 executions of the module's five tests, no hang. The
module's five tests at b4af529f: `test_the_single_thread_outputs_are_what_the_comparison_needs`,
`test_many_threads_raise_nothing_and_every_output_is_its_own`,
`test_the_hostile_inputs_stay_clean_in_every_thread`,
`test_the_whole_conversion_gives_each_thread_its_own_document`,
`test_a_wrong_result_is_never_kept_in_the_conversion_cache`.

CI: every one of those batches was pushed and its Tests job read green by the
Senior Manager or the Release Engineer (batches 14–21: the push packages
`~/Documents/Projects/GAP-beta-batch-NN-push-package.md` STATUS lines; batches
22–26: the Senior Manager's notes, Ran 8266 / 8286 / 8311 / 8323 / 8348 OK, 0
FAIL). Nothing in any record since 2026-10-08 mentions a hang of this module.

## What this does not say

The cause of the one 139 s hang in the author's second series (2026-10-08, before
the dump existed) is still unknown. The deadline in the test is 120 s; the hang
was reported at 139 s by the series script's own clock. If it recurs, the run's
log now carries every thread's stack at the deadline, which is what would be
read first.

No code changed for this row. No test run was made for it.
