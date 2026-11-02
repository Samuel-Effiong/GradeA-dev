# H-276 mutation re-run: expectation (committed before the run)

First run on 62ee5fd1 (mutation_run_62ee5fd1.log; its summary lines say "M-H273-" because of a
label copied from the H-273 script, fixed now): 8/9 KILLED, M-3 SURVIVED, every restore sha256 OK.
I chose not to accept M-3 as equivalent: two refreshes racing on one token can leave a
never-blacklisted refresh token for the same session, and the refresh endpoint must refuse it
itself. New test RefreshGuardTests pins that.
Re-run `python mutants.py 3`. Expected: KILLED, restore sha256 OK.
