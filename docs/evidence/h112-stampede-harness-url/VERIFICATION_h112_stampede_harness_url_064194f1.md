# Verification, round 2: H-112, the H-1 stampede harness takes its server and password from variables (0b)

- **Branch:** task/h112-stampede-harness-url at **064194f1**: 7a32e6fe (rejected by v2, see VERIFICATION_h112_stampede_harness_url_7a32e6fe.md) plus one commit, on task/beta-batch-7 085adecd.
- **Against 085adecd, three files:** the rebuilt docs/evidence/h1_stampede/harness.tar.gz, line 1 of SHA256SUMS.txt beside it, and docs/evidence/h112-stampede-harness-url/EVIDENCE.md.
- **Verifier:** v2 (independent), 2026-10-05. No test run and no slot.
- **Verdict:** **VERIFIED-WITH-NOTES**
- **SM rule kept:** no form of the password was printed, copied, written to a file or tested. Every check was made by v2's own program (GAP-v2-handover/vf_h112_check2.py, with one short follow-up), which prints booleans, counts, line numbers and lengths, and prints a new line only after checking it holds no literal. Output: runs/h112_064194f1_check.txt. 0b's credscan.py was not used.

## The rejection is answered
| v2 asked (round 1) | At 064194f1 |
|---|---|
| Line 14 of `run_followup.sh` takes its value from the environment | It exports `PGPASSWORD` from a required variable and stops with a message if it is unset. Line 15 (`psql` as the postgres user) is unchanged |
| The search covers every form | v2's program takes the forms from the original archive by pattern: the string in the URL password position, its percent-decoded form, and that form encoded again. Two distinct strings, 12 and 10 bytes |
| The scan covers assignment forms inside archives | v2's own scan does (SM ruling of 2026-10-05); 0b's record describes the same for its tool, with its limits |
| The evidence's counts are corrected | One password, two forms, four lines, in a table; the first attempt and why its checks passed are told |
| The two sibling archives are checked | Cleared by v2's own program (below) |

## One password in two forms: the proof, by program
- In the original archive there is one distinct string in a URL password position and one distinct literal value assigned to a `PASS…` name.
- The URL string, percent-decoded, **equals** the assigned value. The assigned value, percent-encoded, **equals** the URL string. Both comparisons printed True; neither value was printed.
- In the original archive the lines holding any form are exactly `run_all.sh` 28 and `run_followup.sh` 14, 32 and 48.

## Checks (0b's list, repeated independently)
| # | Check | Result |
|---|---|---|
| 1 | Members, original (085adecd) against new: count, names, order, modes, owners, times, types | 8 and 8; identical |
| 1 | The six other members | Byte-identical by sha256 |
| 1 | `run_all.sh` | Same 52 lines; differs on exactly line 28 |
| 1 | `run_followup.sh` | Same 65 lines; differs on exactly lines 14, 32 and 48 |
| 1 | The three URL lines | The text before the URL is unchanged; the text after the port on the original line equals the text after the variable's expansion on the new line; `STAMPEDE_PG_SERVER` is required |
| 1 | Each of the four new lines | Holds no form, no URL with a password part and no literal assignment |
| 2 | Any form in the new archive | None, in the gunzipped tar and in the gzip bytes. Every form is in the original tar, so the search works |
| 2 | Any form in the two sibling archives (`raw/harness-logs.tar.gz`, `raw/run-outputs.tar.gz`) | None. They were opened to every level (18 and 4 layers) and are unchanged from 085adecd |
| 2 | Any form in any file at the tip, archives opened | None in 2,773 files, of which 174 were opened as compressed files or archives (gzip, bzip2, xz, zip, tar; nested) |
| 2 | Any form in the branch's two commit messages or in EVIDENCE.md | None |
| 3 | `bash -n` on the two new scripts | exit 0, both |
| 3 | `sha256sum -c SHA256SUMS.txt` | 9 of 9 OK; line 1 is the new archive's hash (prefix 24916dc1) |
| 4 | v2's scan of the three archives: URLs with a password part | 0, 0, 0. The original archive: 3 |
| 4 | v2's scan: literal assignments to a name containing PASS, PWD, SECRET, TOKEN or KEY | Sibling archives: 0 and 0. The harness archive: three `token = …` lines in two unchanged Python members (see note 1). The original archive also had the `PGPASSWORD` line |
| 5 | The record | It prints no form and no URL with anything in the password position (v2's pattern: 0 hits in EVIDENCE.md and in both commit messages). It says both forms stay in history from a9baf3e5, that no pushed branch is rewritten without the founder, and states the scan's limits |
| 6 | Nothing runs or imports the harness | Outside docs/evidence nothing names the harness, its scripts, `STAMPEDE_PG_SERVER` or `PGPASSWORD`. "No test run" is right |

## Notes (none blocks)
1. **The three `token = …` lines** (`measure_stampede.py` 94 and 134, `measure_warm_vs_cold.py` 77) match v2's assignment pattern by name. By program: none starts with a quote and each is a call or subscript of a name, so they are code, not literals. The members are byte-identical to the original. v2 did not read them.
2. **History is unchanged by this slice.** Both forms stay reachable on origin/beta from a9baf3e5. The archive of the first attempt (7a32e6fe) stays in this branch's history with the plain form on line 14; it holds nothing the original does not. If the password is in use anywhere, changing it is the remedy, and that is the founder's action.
3. **By the scripts' own use, the value is the postgres superuser's password** on the machine of the 2026-09 measurement. Whether it is real or reused elsewhere is not something v2 tested or may test.
4. **The scripts were not run** by 0b or by v2: they need a Postgres server and the seeded databases of that measurement. `bash -n` checks syntax only; that the harness still works with the two variables set is not shown.
5. **v2's whole-tree search opens five container formats.** A form inside another binary format (a PDF's compressed stream, an image's metadata, an office document other than a zip-based one) would not be found.
