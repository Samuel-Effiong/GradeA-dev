# Verification: H-137, the credential checker moves into the repository and reads what it used to pass over (0b)

- **Branch:** task/h137-credscan-into-repo at **1d47223b** = 233e97d1 plus the base update onto beta d7143538 (batch 11 as pushed). The tool last changed at 71b9807e, its tests at 6a8c1e60; `scripts/credscan.py` and `AutoGrader/tests_credscan.py` are the same bytes at 07c624f8 (0b's third gate) and at 1d47223b (`git diff`, 0 lines). Tool sha256 at the tip 9705d1214627e8b6, tests 720c7f6e13ed562b.
- **The change:** the checker of record, until now a file outside the repository, becomes `scripts/credscan.py` with a test module. Three things it passed over in silence are now read or reported: a line over 4000 characters (read around each of its words, and counted), a fourth archive inside three (reported as not opened), a file with a NUL byte in its first 4096 bytes (counted as not read). No application code, no migration, no settings.
- **Verifier:** v2 (independent), 2026-10-07. Two slots from 0b: 13:24:59 to 13:29:43 WAT (three probe runs, six whole-tree scans, four comparisons; 1-minute load 7.91 to 4.78, nothing timed) and a short repeat 13:35:13 to 13:35:15 (three probe runs, on the Senior Manager's word).
- **Verdict:** **VERIFIED-WITH-NOTES.** The move changed nothing; the tip loses no row the tool of record gave; what was passed over is now read or said; the fault v2 found by reading is cured and the cure is seen at every one of 61 positions. The notes are a miss of v2's own in the first run, seven rows judged by 0b, one loud-side behaviour and one stated limit.

## Found by reading, before any run
v2 read the first fix (46ef23ee) line by line before asking for a slot and found that it could HIDE a bare `NAME=value` on a long line. The first fix read a stretch of text around each of the five words on the line. When an earlier word stood about 400 characters before the value, that word's stretch ended inside the value: the cut piece either matched as a shorter LITERAL (a quiet untruth about the length) or, cut to a few characters, was taken for a code expression, and the name was then marked as tried, so the whole value was never looked at again.

- 0b confirmed it and cured it at 8d78a179 (tests first): the name is followed back to its start and forward to its end and the pattern is tried from the name, not over the stretch before it.
- v2's second point, also by reading: with many blanks between a name and its sign the VALUE can still end past what is read. 0b cured it at 71b9807e (tests first, 6a8c1e60): such a value is given its own row, `cut | VALUE-CUT-AT-WINDOW`, with the length of the part seen. Loud, not silent.

## v2's probe: three versions of the tool, every expectation written first
`vf_h137_credscan_probe.py` calls the tool's own scan function on bytes made at run time. The planted value is made from parts and random hex, never written to disk, never printed; the output is counts, lengths and shape names. It ran against the tool of record (bdbd2e3d5b0e4b70), the first fix (f1b124bfca081c7a) and the tip (9705d1214627e8b6).

### The first run was spoiled by v2's own miscount
The probe said the planted value is 12 characters. It is 13 (`"w"` + 8 hex + 4). So in the run of 13:24:59 the probe on the first fix and on the tip both ended "not as written" (exit 1) although the tool was right each time, and the sweep, which compared each answer with "a LITERAL of 12", called every distance wrong and showed nothing. Only the run on the tool of record was as written (it sees nothing on a long line, so no length entered). This is the same miscount v2 made in H-136. Reported to 0b and the Senior Manager, not re-run without the Senior Manager's word. The file as run is handed over unchanged (f7b0db9ac7433066).

### The repeat, length taken from the value
`vf_h137_credscan_probe_r2.py` (0ddf286582e4ddfa) differs in one thing that matters: the length is `len(WORD)`, and the sweep prints what each distance gave. Run 13:35:13, all three exit 0, every judged case as written:

| Case | Tool of record | First fix | Tip |
|---|---|---|---|
| C0 a bare value on a long line, no earlier word | nothing | LITERAL 13 | LITERAL 13 |
| C1 an earlier word's stretch ends 3 characters into the value | nothing | **nothing** | LITERAL 13 |
| C2 the same, 8 characters in | nothing | **LITERAL 8** | LITERAL 13 |
| C3 as C1, value in quotes | nothing | LITERAL 13 | LITERAL 13 |
| SWEEP, distances 380 to 440 (61) | nothing at all 61 | wrong at 12: 408 to 414 a LITERAL of 12 down to 6, 415 to 419 **nothing** | LITERAL 13 at all 61 |
| L1 an address and a quoted value far along a long line | nothing | 1 address, LITERAL 13 | the same |
| L3 a fourth archive inside three | no row | 1 NOT-OPENED row | the same |
| K1 a password part of 767 characters (the value 59 times) on a long line (stated limit) | no row | no address row, 1 long-line row | the same |
| K2 410 blanks between name and sign | nothing | **LITERAL 9** | cut row of 9 |
| K3 416 blanks | nothing | **nothing** | cut row of 3 |

- Rule 19: every case differs between at least two of the three versions, and each was seen to differ in this run. The sweep is v2's proof of the fault and of the cure: the first fix hides the value at five distances and misstates it at seven; the tip gives the whole value at all 61.
- v2 wrote "wrong for 409 to 419" in the first form's docstring; the run says 408 to 419. The repeat's expectation was corrected with the length, before the repeat ran.

## "Moved, not changed", and nothing lost
Six whole-tree scans (`--all`, masked output, each to its own file, each `.err` empty), on d7143538 and on 1d47223b, by the tool of record, the tool as first committed (e67da7daee9d8fa0) and the tip; compared by `vf_h137_compare.py`.

| Comparison | d7143538 | 1d47223b |
|---|---|---|
| tool of record against the tool as moved | the same text, 3316 lines in 1102 rows | the same text, 3318 lines in 1102 rows |
| tool of record against the tip: rows missing | 0 | 0 |
| rows with a lower count | 0 | 0 |
| rows new or with a higher count | 187: 78 long-line, 50 not-read, 59 on lines the old tool passed over | the same |

No address-with-password row is new. Of the 59: 36 code-expression, 14 variable-or-mask, 2 placeholder-word, 7 LITERAL.

## The seven LITERAL rows
The tip lists seven LITERAL rows the old tool did not (or listed with a lower count), all in two documentation files, on lines of 13 000 to 18 000 characters: `docs/CODEBASE_AUDIT_SECTIONS.md` (length 4, one more hit than before) and `docs/backend/backend-reference.html` (lengths 4, 6 six times, 7, 9, 11 three times). v2 reported them masked and opened neither file. They are on the pushed beta already and on origin/staging (0b); this change does not create them, it shows them.

By the Senior Manager's ruling 0b judged them without seeing a value: the tip tool's `--lines` list on d7143538, and the text around each hit with every matched value replaced by its length BEFORE printing (`~/Documents/Projects/GAP-0b-runs/h137_lines_d7143538/`: context_masked.py 17d7375b93adb09b, context.txt 8e0b148eb448b227). 0b: none is a credential.

v2 read the script and the masked text and agrees: the script masks every pattern match in the window and every unbroken run of 20 or more before it prints; line 311 of the audit document is the heading "PRODUCTION GATE PASSED" followed by the heading's own closing marks (the other, line 314, "strict gate PASS:", is under 4000 characters and was listed before); the twelve in the HTML are a minified diagram library's error objects of the form `token:"<N chars>"+i`, a parser's token name. The Senior Manager accepted the judgement; nothing goes to the founder. Classing such a `token:` as code is not in H-137; it is the new LOW row H-156.

## 0b's gates, read by v2 from the raw logs (not repeated, rule 15)
Third gate, at 07c624f8, 12:54:40 to 13:00:44, load 6.74 at the start and 5.61 at the end (`console_07c624f8.txt`). v2 checked each committed raw log against `raw_logs_sha256_07c624f8.txt`: the four gzipped logs, the mutation log and all 26 mutant logs match.

| Gate | The raw log |
|---|---|
| red run, tests against the tool as moved | Ran 28, FAILED (failures=167), 16 distinct tests |
| red run, against the first fix | Ran 28, FAILED (failures=15), 4 distinct tests |
| red run, against the second fix | Ran 28, FAILED (failures=2), 1 test (the cut-value test) |
| the module and the guard list | Ran 314 tests in 158.932s, OK |
| 26 mutants | 26 killed, none survived, none broken; each has a Ran line and its named failing tests; L9 is the fault v2 found |

## Notes
1. **The first run's miscount (v2's miss).** Above. Lesson kept: a made value's length is computed from the value. One leftover of it stands in both probe files' notes: K1 is described as "708 characters" (12 times 59); the code multiplies the value itself, so the part is 767 long and the case is unaffected. The files are handed over as run, so the wrong figure stays in them and is corrected here.
2. **The cut row is given for names that hold KEY only and for test files too** (probe line I1: a cache-key name with 410 blanks gives a cut row of 9 at the tip, where on a short line such a name is only counted). This is the loud side; no change asked.
3. **A stated limit, shown as a fact (K1):** a password part of an address longer than what is read around a word, on a long line, is not seen as an address; the line is counted as long. It is in the tool's notes.
4. **The module and guards ran at 07c624f8, before the base update.** The tool and its tests are the same bytes at the tip, and v2's six scans ran the tip tool over the merged tree. The guards over the merged tree are batch 12's full run's to show; v2 did not ask for a repeat.

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record; `vf_h137_credscan_probe.py` (f7b0db9ac7433066, as first run), `vf_h137_credscan_probe_r2.py` (0ddf286582e4ddfa), `vf_h137_compare.py` (52e6df1128d8b1c8), `vf_h137_run.sh` (a10f5f58b711b2fa), `vf_h137_run_r2.sh` (1704e5ed632493cd); `runs/h137_1d47223b.status` (4348725ed04d3495), `runs/h137_1d47223b_script.out` (305f0b82027703cb), `runs/h137_1d47223b_r2.status` (245c0a75485a4d32), `runs/h137_1d47223b_r2_script.out` (a678343a809026ed); the two logs folders in one tar (three probe outputs each; six masked scans, six empty error files, four comparisons). No file holds a value: the scans are the tool's masked report, the probes print lengths and counts.
