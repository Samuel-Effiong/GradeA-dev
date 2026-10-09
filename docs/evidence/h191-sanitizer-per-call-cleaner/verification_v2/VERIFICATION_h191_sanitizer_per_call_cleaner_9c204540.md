# Verification by Verifier 2 (v2): H-191, the HTML sanitizer shares one bleach Cleaner between threads

Tip verified: `9c204540` (code `bf0d7632`; the later commits change only docs and a docstring, diff read).
Run: 2026-10-08 16:42:53 to 16:48:43, Release Engineer's slot, load 4.00 at start. Scratch worktree
detached at the tip, probe and the beta `3f2ad13e` blob of the converter copied in and removed after.
Probe, runner and run script were written before any run (checksums: probe `aaca9c833fe961ca`,
runner `0740c94dcb96de0c`, script `a55d6652ad3fa1ce`). I did not repeat the author's gates or regression (rule 15).

## Word: VERIFIED-WITH-NOTES

## What I checked
1. **The cure changes nothing but threading.** `_new_cleaner()` has the same six arguments as the old module-level
   Cleaner (read). Eight single-thread tests compare the cured `sanitize_editor_html` and the whole conversion
   with the OLD code's output over a corpus touching each setting (comments, URL schemes, CSS, disallowed tags,
   attribute allowlist, raw-text and control characters, empty input), each with a deciding value read from the
   new output. Baseline: Ran 9 tests OK (these 8 plus one threaded test).
2. **Each setting is held** (7 faults, each judged by my probe alone, failing set written beforehand): comments kept
   -> a1; protocols emptied -> a2; css sanitizer off -> a3; strip off -> a4; attributes emptied -> a2, a3, a5;
   raw-text step dropped -> a6; control-character step dropped -> a6. All seven KILLED AS WRITTEN (the
   whole-conversion test a8 also fell each time; I had allowed it). Restores verified, 8 of 8.
3. **Shared Cleaner put back** (M8): my own threaded test (8 threads, own inputs, own deadline) failed, at its
   FIRST assertion: 8 threads still running at the 60 s deadline (`8 != 0`), 60.3 s. That is the wedge, not a wrong
   text. It is the same kind of kill as the author's S1 (a timeout).
4. **Reading checks of the evidence** all matched: bleach's own "not thread-safe" docstring; the reset and
   `getFragment` lines in the vendored html5lib; Dockerfile (9 workers, 4 threads, gthread); `start-worker.sh`
   (no `--pool`); `lru_cache(64)` at converter line 692; the six places that save converted text; the 14-of-15
   count (4+5+5). One sentence was wrong and d5 fixed it in `9c204540`: the serializer (and walker) are built in
   `Cleaner.__init__`, so they were shared on the old code too; only the markup-removing filter is per call.

## What my observation run showed (differs from the wording "mixed text NOT observed")
Part C of my probe ran the OLD shared Cleaner (the `3f2ad13e` blob) under 8 threads, thread switch interval 1 us,
60 s deadline, three times; it asserts nothing and counts. Threads were still running at the deadline in all three.

| Run | Texts returned | Differ from the single-thread text | ... and carry ANOTHER input's marker | Calls that raised |
|---|---|---|---|---|
| 1 | 3 | 3 | 2 | 3 (AssertionError, IndexError) |
| 2 | 66 | 66 | 28 | 79 (AssertionError, IndexError, ValueError) |
| 3 | 471 | 470 | 213 | 1011 (seven kinds) |

So, on the old code, under this stress, a call returned text that contained a different caller's text.
**Can the marker test give a false positive? No, by construction (read from the probe):** each of the 10 inputs
carries markers of the form `VF<nn>-` (head, body, li, td) with its own two-digit index `nn`, and its remaining
text (`x<i>`, `h<i>`, `q<i>`, `https://e.example.com/<i>`) holds no `VF`. So a CORRECT output of input k contains
`VF<k>-` and no other input's `VF<j>-` (j != k); and the count only looks at outputs that already differ from the
single-thread output. A foreign marker in such an output is text that came from another input. The test can miss
mixing that carries no marker (a bare cell or link from another input), so the counts are a floor, not a total.

**Wording ruled by the Senior Manager (2026-10-08, after this run):** "MIXED TEXT OBSERVED on the old shared
Cleaner under a stress probe by Verifier 2 (8 threads, switch interval 1 microsecond, three 60-second runs on the
module as at 3f2ad13e): outputs differing from the single-thread output 3, 66, 470; of those, outputs carrying
ANOTHER input's marker 2, 28, 213; calls that raised 3, 79, 1011. Crash and wedge observed by d5 and 0b (5 of 5).
NOT shown: that it happened in production, that it reached the database, the rate under real load; the probe
counted and saved no sample text; the marker check is a substring test." No second slot for samples; the probe
is kept (`tests_vf2_h191_probe.py`, Part C) so a sample run can be made later if the user asks.

**My catch:** the serializer (and walker) are built in `Cleaner.__init__` and were shared on the old code; found by
reading bleach 6.4.0 `sanitizer.py` lines ~76-93; d5 corrected the evidence in `9c204540`.

**Limits, said plainly:** my probe kept counts only, no sample text, so the mixed text is not shown here, only counted;
the marker test is a substring test; it ran the old module in a test process, not the live app; it is a stress
shape and says nothing about how often, or whether, it happened in production or reached the database. The
author's own tests could not have seen this (they stop at the error-list assertion). The Senior Manager's
wording ("mixed text NOT observed in any run") should be changed by the Senior Manager to: "observed once, by
Verifier 2's stress probe on the old code, as counts".

## Notes
- N1: S1 (author) and M8 (mine) are killed by a wedge or timeout only; no run showed the cured code failing for
  a reason other than the deadline, 14 of 15 green over the author's series; the one red was the author's own
  120 s deadline on a slow test. The tests depend on timing and a loaded machine could trip the deadline.
- N2: the allowlist-parity tests (my a1 to a8) are not in the branch; they are in the files below. Adopting them
  would hold each Cleaner setting, which the branch's own module does not (it holds only `onerror`).
- N3: `bleach.clean(...)` at `assignments/services.py:584` builds its own Cleaner per call: not affected.

## Files
`probe`, `runner`, `run script`, driver logs, mutant logs (gzipped), obs counts: `logs/`.
