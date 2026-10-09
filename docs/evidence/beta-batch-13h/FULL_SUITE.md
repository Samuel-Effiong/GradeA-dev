# Batch 13h: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-08. 0b started the run and read its result.

**What batch 13h is:** the pushed beta tip `3f2ad13e` (batch 13) plus ONE row, H-191, the text cleaner made per call: the merge `f53e1f0b` of `b7bccf3c` (Hardening Engineer d5; Verifier 2 VERIFIED-WITH-NOTES), then the docs commit `e678c051` (docs/HARDENING_BACKLOG.md only). Beta's tip is an ancestor of `b7bccf3c`, so the merge was clean. Verifier 2's record commit `c691b10f` was checked by 0b against his folder `h191_for_commit_9c204540`: 24 files, byte-identical (the committed copies differ only by the `.txt` and `.gz` names the hooks need). Against beta the only non-docs files that change are `assignments/prosemirror_converter.py` and `assignments/tests_sanitizer_threads.py` (since the gated tip `bf0d7632`, the test's docstring only).

**What was observed (the Senior Manager's ruled wording):** the crash and the wedge were OBSERVED 5 times out of 5 on the old code, in the author's runs. Mixed text (one user's text in another's output) was OBSERVED by Verifier 2's probe (counts 3/66/470, 2/28/213, 3/79/1011), with the limit that no sample text was saved. Verifier 2's notes: the kills of the shared cleaner are by deadline or timeout, the tests are timing-dependent, his parity tests are not in the branch.

**Command:** `strict_bundle.sh e678c051 b13h Grade-Automator-Plus-beta-batch-13h task/beta-batch-13h` under rules 12, 13, 16 (revised), 17 and 18; a copy of the script is beside this file.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `e678c051` | 2026-10-08 17:25:11 to 17:33:27 (460 s of tests; 496 s wall) | **Ran 6252 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (they sum to 6252): ai_processor 852, assignments 710, AutoGrader 692, billing 2130, classrooms 479, dashboard 270, students 438, users 681. Against batch 13's 6247: assignments +5, the five tests of `assignments/tests_sanitizer_threads.py`; every other app is unchanged. Skipped 28, as in every run since batch 11.

**Load average (1 minute):** 1.62 at the start (3.97 and 0.40 a few minutes earlier), 6.68 at the end.

**The log:** `strict_b13h.log.xz` (8,071,778 bytes unpacked, sha256 `e7fd5a9b13610bd82a18f0fe0255ccee07055b68e2ef596c666e08be55a8c903`); `strict_b13h.summary.txt` is the script's summary.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function on each file added with this record, values masked):** the record, the script copy and the summary: 0 hits. The packed log: no address with a password part; 6 literal rows (the two known warning lines from the AI module's test, and 4 under names holding only the word KEY), 5 code expressions, 5 placeholder words, 9 variables or masks, the same as batch 13.

**Not proof of the race:** this run being green does not show the race is gone; the race is timing-dependent. It shows the cure breaks nothing else.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
