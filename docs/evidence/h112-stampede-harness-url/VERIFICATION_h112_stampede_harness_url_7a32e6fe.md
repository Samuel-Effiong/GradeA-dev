# Verification: H-112, the H-1 stampede harness reads its server URL from a variable (0b)

- **Branch:** task/h112-stampede-harness-url at **7a32e6fe**, one commit on task/beta-batch-7 085adecd. Three files: the rebuilt docs/evidence/h1_stampede/harness.tar.gz, line 1 of SHA256SUMS.txt beside it, and docs/evidence/h112-stampede-harness-url/EVIDENCE.md.
- **Verifier:** v2 (independent), 2026-10-05. No test run and no slot.
- **Verdict:** **REJECTED**
- **SM rule kept:** the string was never printed, copied, written to a file or tested. Every check was done by program (GAP-v2-handover/vf_h112_check.py and two short follow-ups), printing booleans, counts, line numbers and lengths only. The output is in runs/h112_7a32e6fe_check.txt.

## The finding: the same password is still in the new archive, in its plain form
- **Where:** `run_followup.sh` **line 14** in the new archive: an `export PGPASSWORD='…'` line with a literal value of 10 bytes. The line is unchanged from the old archive. It is the only `PGPASSWORD` assignment in the archive; line 15 uses it for `psql -h 127.0.0.1 -U postgres`.
- **It is the same password.** The string H-112 removed from the three URL lines is 12 bytes and holds a percent escape: it is the URL-encoded form. Percent-decoded, it equals the line 14 value exactly; the line 14 value, percent-encoded, equals the removed string exactly. Both comparisons were made by program and printed True.
- **Why the slice's own checks pass:** the search for "the old string" looks for the 12-byte encoded form, which is indeed gone. The scan tool reports only a URL with a user and a password before the host, and EVIDENCE.md says so in its limits. A `KEY='value'` line is outside both.
- **So the slice does not do what its record says:** after this commit the tip of the tree still holds the password, readable by anyone who opens the archive.

## What v2 asks for before a new verdict
1. Line 14 of `run_followup.sh` takes its value from the environment (a required variable, as on the URL lines), or the line goes if `STAMPEDE_PG_SERVER` makes it unneeded for line 15's `psql`.
2. The "old string" search covers every form: the string as found, its percent-decoded form and its percent-encoded form, in the gunzipped new tar.
3. The scan covers assignment forms inside archives: at least `PGPASSWORD=` and `PASSWORD=`/`SECRET=`/`TOKEN=` with a quoted or bare literal. v2's loose pattern found four hits in the new archive: this one, and three `token = …` lines in two unchanged Python members that are code, not literals.
4. EVIDENCE.md: "One distinct string of 12 characters" and "3 occurrences" are corrected (one password, two forms, four lines), and the history paragraph says both forms stay reachable from a9baf3e5.
5. The other archives beside it (`raw/harness-logs.tar.gz`, `raw/run-outputs.tar.gz`) are checked for the decoded form too. v2 found no occurrence of the decoded form in the raw bytes of any of the 2,773 files at the tip, but that search does not open compressed files.

## What v2 checked and found correct (0b's list)
| # | Check | Result |
|---|---|---|
| 1 | Members old against new: count, names, order, modes, owners, times, types | 8 and 8; identical |
| 1 | The six other members | Byte-identical by sha256 |
| 1 | `run_all.sh` | Same 52 lines; differs on exactly line 28 |
| 1 | `run_followup.sh` | Same 65 lines; differs on exactly lines 32 and 48 |
| 1 | On each of the three lines | The text before the URL is unchanged; the text after the port on the old line equals the text after the variable's expansion on the new line; the new line holds no URL with a password and names `STAMPEDE_PG_SERVER` with a `:?` (required) expansion |
| 2 | The removed (encoded) string in the new archive | Absent from the gunzipped tar and the gzip bytes; present in the old tar (so the search works). Absent from the commit message, EVIDENCE.md and SHA256SUMS.txt. The decoded form is absent from those three as well |
| 3 | `bash -n` on the two new scripts | exit 0, both |
| 3 | `sha256sum -c SHA256SUMS.txt` | All 9 lines OK |
| 4 | v2's own URL-with-password pattern | New archive: 0 hits in members and in the raw tar. Old archive: 3 (so the scan works) |
| 5 | The record's claims | It says the string stays in history from a9baf3e5 and that no pushed branch is rewritten; it states the scan's limits; it prints the string nowhere |
| 6 | Nothing runs or imports the harness | Outside docs/evidence the only references are three mentions of the measurement write-up in two documents. No code, test, CI file or script names the harness, its scripts or its variables. "No test run" is right |

- **0b's credscan.py was not run by v2:** v2 used its own program, as the request allowed.
- **Disclosed:** v2's first version of check 1 looked for the port on the new lines too and reported two false failures per line; the new lines replace the whole server URL, port included. That was v2's mistake, corrected before this record; the comment in the program says so.

## Notes
1. **For the founder, through the SM:** the question "is it a real password" now has one more fact. The value is used as the `postgres` superuser's password for a server on 127.0.0.1 in the measurement of 2026-09. If that password is used anywhere else, changing it is the remedy; removing lines does not take it off origin/beta's history.
2. **EVIDENCE.md writes the old URL with a masked label in the password position** (twice). It is not a password and v2 does not count it as a defect, but the SM's rule on H-97 was that no committed file holds a URL of that shape, even with a placeholder. The SM may want it reworded ("a URL with the postgres user and a literal password before the host").
