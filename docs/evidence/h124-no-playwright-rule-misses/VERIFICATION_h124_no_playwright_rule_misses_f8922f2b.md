# Verification: H-124, the no-Playwright-at-import rule follows other names, methods, classes and decorators (d5)

- **Branch:** task/h124-no-playwright-rule-misses at **f8922f2b**, on task/beta-batch-9 9fb6d4fe (H-110 and H-123 merged). For batch 9. The last change to the guard module is 0dffc395; 7134815d adds the runner, b03f0569 is the base update, and c9ae46b3, b41de114 and f8922f2b add only docs/evidence/h124-no-playwright-rule-misses/.
- **The change:** test-only. One guard module, `AutoGrader/tests_no_playwright_at_import.py` (+403/-43, blob 1b466b7c), and a mutation runner in the evidence folder. No production code, no migration, no settings change.
- **Verifier:** v2 (independent), 2026-10-06. Two pre-reads (at c9ae46b3 and 7134815d) and one slot from 0b, 14:10:06 to 14:10:44 WAT, load 6.60 at the start and 6.52 at the end (1-minute) (not timing-sensitive).
- **Verdict:** **VERIFIED-WITH-NOTES**

## The change, as read
The guard reads every test module's source and names any line that would start Playwright's driver while the module is being imported. H-118's version saw a starter only under its own name, called directly or through the module's own functions. v2's H-118 record listed four misses. This row answers them.

- **Other names:** a starter imported under another name; a name bound to a starter or to a function, method or lambda that reaches one; `staticmethod(x)` and `classmethod(x)` read as `x`.
- **Methods:** a method that reaches a starter counts when asked of its class or of an object, and by its bare name inside its own class body.
- **Classes:** a class whose `__init__` or `__new__` reaches a starter counts when it is called by its bare name.
- **Decorators:** a decorator applied without brackets is treated as a call.
- **`getattr`** with the name written out is read as asking for that name.
- **The scan's first look** is now a plain search for the starter's name, so a file that names a starter only beside a quote or a bracket is read.

## What v2's pre-reads found, and where each point stands at f8922f2b
Method: sample sources with v2's prediction written first, then the rule called on each in plain Python (the rule's functions loaded from the git blob; no test runner). 32 samples; all answered as predicted at c9ae46b3, at 7134815d and again at f8922f2b.

| Point | At f8922f2b |
|---|---|
| P1: a regression at c9ae46b3. A module function wrapped with `staticmethod` under its own name and asked of the class was named by H-118's rule and not by the new one | **Fixed** (0dffc395), with two red samples first (4e22fda5) and mutant N15 |
| P2: twelve shapes that start a driver at import, seen by neither rule and not listed as limits | SM's ruling (the later one, about 11:02): the three plausible ones are **followed** (a class made at import, a bracketless decorator, a bound lambda), each with samples and a mutant (N16 to N19); the other nine are **pinned as limits** |
| P3: the sentence "a name bound inside one function counts everywhere" had no sample | **Pinned** as a known false alarm, with a second one for a common method name |
| The evidence said `functools.partial` "is in fact caught" | **Corrected:** only when called in the same expression; bound to a name and called later it is a pinned limit |
| Second pre-read: four further limits of the new class step, and three safe-side false alarms | **Listed** in the evidence, not pinned (the module was frozen for its battery). v2 asks for no pin: see N2 |

- **The SM ended shape-chasing for this row:** anything found later is a limit. v2 agrees with the reason: this rule is a cheap first net, and the fresh-interpreter test is the real one for the one module that uses a browser.

## d5's gates, read by v2 from the raw logs (not repeated, rule 15)
| Gate | The raw log, as committed |
|---|---|
| (r) the two red commits, the guard module | 77628465: Ran 18 tests, FAILED (failures=15). 4e22fda5: Ran 22 tests, FAILED (failures=8). Both red sets as written beforehand |
| (a) the guard module and the repo-wide guard list, at 7134815d | Ran 272 tests in 311.842s, OK, exit=0 |
| (b) 22 mutants, at 7134815d | 22 of 22 killed; each failing set equals the list written beforehand |
| (c) the owning app, AutoGrader, `--parallel 2`, at b03f0569 on the batch 9 base | Ran 573 tests in 327.581s, OK (skipped=2), exit=0; the watchdog saw no stall. The two skips are the fork-isolation tests, which skip under `--parallel` |

- **The battery is on the final module (rule 17's addendum):** the module is blob 1b466b7c at 7134815d, b03f0569 and f8922f2b. H-118's three mutants of the rewritten function are run again in it (G1 to G3).
- **(a) and (b) ran on the batch 8 base, (c) on the batch 9 base.** The base update changes no file of this row. What it adds to the tree are H-110's and H-123's test modules; (c) runs the guard's two tree-wide tests over them and both pass. v2's own call of the rule on the three test modules of f8922f2b that mention a starter names no line.
- **(a) and (b) ran on a loaded machine** (1-minute load 6 to 12). Nothing in them is judged by the clock.
- **The laptop was suspended** from 13:26 to 13:59, ten minutes after (c) had ended. It touches no run.
- **Nothing but evidence after b03f0569,** and against 9fb6d4fe the branch changes the guard module and its evidence folder only.
- **Credential shapes** in the branch's files, archives opened (113 texts): no URL with anything in the password position, plain or encoded.

## v2's run (14:10:06 to 14:10:44, at f8922f2b)
Expected results were written in the runner by test name at 13:10, before any run; 0b read the runner and the script before the grant.

| Step | Result |
|---|---|
| Baseline: the whole guard module | Ran 22 tests in 8.229s, OK |
| V1 one pass in place of the fixed point | **KILLED** by exactly `test_the_rule_sees_each_shape` (the sample "through two functions"). Ran 15 tests in 0.449s, FAILED (failures=1) |
| V2 bracketless decorators followed on functions only, not on classes | **KILLED** by exactly `test_the_rule_follows_a_decorator_applied_without_brackets` (the sample "on a class"). Ran 15 tests in 0.548s, FAILED (failures=1) |
| V3 in a class body every own method counts by its bare name (predicted to survive) | **SURVIVED**, as predicted. Ran 15 tests in 0.528s, OK |

- **Rules 16, 13, 12** wrap both steps. **Rule 17:** `PYTHONDONTWRITEBYTECODE=1` and `python -B`; `__pycache__` of the module's directory deleted before the baseline, before each mutant and after each restore; every restore equals the commit's blob by sha256. **Rule 18:** every inner run wrote straight to its own file with stdin from the null device.
- **Load:** 1-minute load 6.60 at the baseline's start, 6.51 between the steps, 6.52 at the end (the other project's browser suite was starting). Nothing in this run is judged by the clock. No other run of the team's ran beside it.

## Notes
- **N1 (what the rule is).** A reading of one file at a time, by names. It does not know scopes or types, and it does not follow a helper in another module. The evidence lists fifteen shapes it does not see (eleven pinned) and seven safe-side false alarms (four pinned). The fresh-interpreter test is the net for the one module that uses a browser today.
- **N2 (the unpinned lists).** K1 to K4 and K8 to K10 are stated in the evidence and not held by a sample. v2 asks for no pin: none is a regression, the SM closed the row's scope, and a pin would have changed the module after its battery.
- **N3 (V3).** With every method of a class counting by its bare name in the class body, not only those that reach a starter, all tests pass. The mutant makes the rule stricter, never blinder: it would name a harmless method called by its bare name in its own class body. No sample holds that line, and no test module of the tree has the shape in a file that names a starter. A sample in the "allows" test would pin it. v2 asks for none in this row.
- **N4 (cost of the wider rule).** It errs towards naming too much. A test module that gains a method, a bound name or a class with a browser-starting `__init__` can get an unrelated import-time call named, with a message that points at Playwright. The fix then is to rename or to move the call, and the evidence's list of false alarms says which shapes do it.

Files: `~/Documents/Projects/GAP-v2-handover/` `vf_h124_mutants.py`, `vf_h124_run.sh`, `h124_preread_samples.py`, `h124_preread_samples_round2.py`, `h124_preread_samples_c9ae46b3.out.txt`, `h124_preread_samples_7134815d.out.txt`, `h124_preread_samples_f8922f2b.out.txt`, `runs/h124_f8922f2b_baseline.log`, `runs/h124_f8922f2b_mutants.log`, `runs/h124_f8922f2b.status`, `runs/h124_f8922f2b_mutant_logs.tar.gz` (each inner run's whole output).
