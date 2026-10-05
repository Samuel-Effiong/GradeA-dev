# Verification: beta-batch-7, Gate 1 (batch level) @ 27b0d2e0

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `141c8031` (bundle 6). **Tip:** `27b0d2e0`; the code tip is `7ac6fb48` (the H-107 merge). `01e87ed1` adds only the backlog rows and four document files, and `27b0d2e0` only 0b's full-run record (both docs). **Date:** 2026-10-05.

**Verdict: VERIFIED.** Bundle 7 is exactly its seven verified items, merged cleanly, and its one strict full run passed on the exact final code tree. Nothing is required before the push. The founder still has to confirm that specific push, and the package should carry the notes below; N1 is the one the founder must read.

I ran no test for this gate (rule 15). The checks are git and record checks, my own run of 0b's masked credential scan, and my own reading of 0b's full-run log.

The bundle closed without H-98 and H-110; both move to the next bundle.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | origin/beta `141c8031` is an ancestor of `27b0d2e0`: a fast-forward, 66 commits, 30 non-docs files. |
| Evil merges | All **7** first-parent merges in `141c8031..27b0d2e0` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree) and hold nothing that is on neither parent: `9a98714f`, `085adecd`, `cb0927f1`, `87389416`, `94f08711`, `c823cdca`, `7ac6fb48`. |
| Direct commits | Two, both docs only: `01e87ed1` and `27b0d2e0` (`docs/evidence/beta-batch-7/FULL_SUITE.md`). |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. The one base-update merge inside an item (H-97) is clean and brings in batch commits only. |
| My records in the tree, byte for byte | H-89, H-91 and H-107: the record, the probe, the mutant and both logs of each match my copies. One file is stored gzipped (N2). |
| v2's records | Present in the tree for H-97, H-112 (both, the REJECTED one and the VERIFIED-WITH-NOTES one) and H-109. I did not byte-compare them. |
| Migrations, models | **None.** 0b's run reports `makemigrations`: no changes detected. |
| Settings, beat schedule, requirements | `AutoGrader/settings.py` changes for H-89 only: the computed switch `LOG_SCRUB_ADDRESSES` and the Sentry start-up with its four hooks. No environment setting is read that was not read before; no `CELERY_BEAT_SCHEDULE` entry; `requirements.txt` unchanged; no management command added or changed. |
| The docs commit `01e87ed1` | Five files, all under `docs/`: the backlog (rows H-105 to H-117 added; H-89, H-91, H-97 and H-101 marked; H-98's row updated) and the four document files. |
| The four document files | **Byte-identical** to the copies in `~/Documents/Projects/GAP-docs/`: the reason-codes reference (`.md` 44,537 bytes, `.pdf` 372,114) and the data-privacy summary (`.md` 19,046, `.pdf` 105,845). I compared them; I did not review what they say (N6). |
| After the run tip | `01e87ed1..27b0d2e0` changes the full-run record only, and `7ac6fb48..27b0d2e0` differs in **0** non-docs files. So the strict run's tree equals the push tip's code. |

## The seven items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-97 Redis test clean-up in every database (test tooling) | v2, VERIFIED-WITH-NOTES | `1e407f75` | `991f24dc` | `9a98714f` |
| H-108 the catcher comment (comment only) | 1a, static read OK | `c4aed446` | `c4aed446` | `085adecd` |
| H-112 the stampede harness archive (docs) | v2, VERIFIED-WITH-NOTES (first attempt REJECTED) | `064194f1` | `f81ba90f` | `cb0927f1` |
| H-91 ids-only logs everywhere | 1a, VERIFIED | `63765f80` | `6269ffc9` | `87389416` |
| H-89 the log and Sentry scrubber | 1a, VERIFIED-WITH-NOTES | `26774ba8` | `830ea9d5` | `94f08711` |
| H-109 the Redis hygiene test URLs (test only) | v2, VERIFIED-WITH-NOTES | `b43ea36e` | `f7d71876` | `c823cdca` |
| H-107 the parallel run that hangs (test runner) | 1a, VERIFIED-WITH-NOTES; the test-only delta checked by reading | `9a4bcd1f`; delta `61307784` | `178a3d15` | `7ac6fb48` |

## Each mutation battery is against its module's last test change (rule 17's addendum)
| Item | Last change to its tests | Battery |
|---|---|---|
| H-89 | `8fd5dc9d` (the two scrubbing test modules) | 36 of 36 at `8fd5dc9d`; my mutant at `26774ba8`. |
| H-91 | `6a82ef41` (the guard moved under `AutoGrader/`) | 14 of 14 at `2c336a50`, which contains `6a82ef41`. The later base update changed no H-91 test and one H-91 file, in another item's hunks (my H-91 record); my mutant ran at the tip. |
| H-107 | `6370f662` (the wiring test) | 13 of 13 at `6370f662`. |
| H-97 | `d58a65d3` (H-109's edit of the databases test module) | H-97's own battery ran before that edit. H-109 repeats all five mutants on the final module at `d58a65d3`: 5 of 5, each mutant's failing lines identical to the run on the unedited module. The other hygiene test module last changed at `60415c99`, before H-97's battery. |
| H-108, H-112 | none | No test and no battery: a comment, and an archive under `docs/`. |

## The credential pattern scan (widened form; every value masked)
I ran 0b's tool myself on `27b0d2e0` and on `141c8031`, listing every row, and compared the two. My default output is identical to 0b's `scan_27b0d2e0.txt`.
- **URLs with a password part:** none added. The three archive lines are gone. Five remain, all on origin/beta already.
- **One value written two ways** (plain and percent-encoded): 1 group at the base, **0** at the tip.
- **Assignment forms, outside test files:** the archive's one line is gone. **Twelve matches are new, on ten lines, all in verification records, evidence text and my two probe copies.** I read each line with its value masked:
  - Seven are prose in v2's two H-112 records and d5's H-89 evidence: a sentence that names such a form, or an ordinary word followed by a colon. None is a value.
  - Five are in my H-89 and H-91 probe copies: the repository's standard fixture account password (three times, the literal that is in over a hundred test files), a made-up password piece from which my H-89 probe builds its test URL out of parts, and a one-letter stand-in for the MailerLite key setting.
  - None has the removed value's length (10 plain, 12 encoded).
- **Assignment forms in test files:** four new literal lines. One is the same fixture password; two are the made-up password constant of H-89's two test modules, never inside a URL in the source; the fourth is H-109's made-up password for its test URLs, kept apart from any URL in the same way. (Which lines hold the same string I checked by a salted digest, without printing a value.)
- **The four document files:** no URL with a password part, no percent-encoded form and no email address in the two markdown files; the same for the text I extracted from the two PDFs, which the tool does not open.
- **This record** holds no URL with anything in the password position and no assignment form.

**Limits.** The scan is by pattern: it finds the shapes above and nothing else, and it reads the tree at the tip, not the history (N1).

## Gate 10 (0b's strict full run; one per bundle, rule 15, not repeated)
| Tip | Result |
|---|---|
| `01e87ed1` (the final code tree) | **Ran 5686 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. 12G, `--parallel 4`, `--verbosity 2`; mypy and `makemigrations --check` clean first (0b's `docs/evidence/beta-batch-7/FULL_SUITE.md`, `27b0d2e0`) |

I read the full log myself (`~/Documents/Projects/GAP-0b-runs/strict_b7.log`, 7,514,029 bytes; its sha256 prefix `9f961d4b22420a8b` matches the record):
- **The result line** is `Ran 5686 tests in 694.897s`, `OK (skipped=28)`. No line of the log ends in FAIL or ERROR, there is no FAIL or ERROR header, and no expected failure or unexpected success. No `BlockingIOError`, no `OutputNotRead`, no fatal interpreter error, no blocked outbound call.
- **The count checks out exactly, by test id:** bundle 6 ran 5601. This log has 5686 distinct test ids; every one of bundle 6's is among them, and **85** are new. 5601 + 85 = **5686**. (The range adds 86 `def test_` lines; one is inside a script that an H-107 test runs in a child process, so the runner does not collect it.)
- **Where the 85 are:** AutoGrader +82 (H-89's two modules 35 and 16, H-107's two 14 and 4, H-97's 9, H-91's guard 4), billing +3 (H-89's end-to-end module). Each of those modules ran in full.
- **Per-app counts** (my own count of the log's test lines) match 0b's record: ai_processor 817, AutoGrader 551, assignments 628, billing 2097, classrooms 401, dashboard 270, students 268, users 654. They sum to 5686.
- **The skips** are the same 28 tests as in bundle 6's run, all opt-in or environment skips. None of the bundle's new tests is skipped; H-107's forked-worker test ran inside this parallel run.
- **Result lines I could match one by one:** 5,681 of the 5,686 (5,653 ok, 28 skipped). For the other five, other output sits between the test's line and its result. The result line and the absence of any FAIL or ERROR are what I rely on for those.
- **Rule 18 form.** The script (`strict_b7.sh`) sends the run's output straight to the log file with stdin from `/dev/null`; nothing is piped. The watchdog at the side (300 s without a new log line) never fired, the run's exit status file holds 0, and the summary reports no suspend. Rules 12, 13, 16 and the bytecode setting of rule 17 are in the command.
- **What this run adds.** It is the first run of the whole of assignments, students, ai_processor, classrooms and dashboard on the combined bundle, and the first full parallel run with H-107's fixes in. With its output in a file it does not put the stream fix against a full pipe; H-107's own evidence and my probes do.
- **It was the only strict run of bundle 7, on the final code tree, and it passed.**

**Not done by me:** I did not run the commit hooks over the batch range. Each of my three items passed them over its own range, and 0b's whole-repo mypy passed.

## Notes (not blocking)
**N1 (founder: two facts about the history this push carries).** Both are in the package's first section; I confirm them from git.
- **A password that looks real stays in origin/beta's history.** `docs/evidence/h1_stampede/harness.tar.gz` has held it since `a9baf3e5` (2026-09-15), already pushed. H-112 takes it out of the tree; nothing in this bundle takes it out of the history. If it is still in use anywhere, it should be changed.
- **H-89's branch history holds made-up credential lines** (`5bd912ad`, test log lines with an invented password), removed from the tree by later commits; and one wrong sentence of mine about the first point (`d2cdfbe8`), corrected on top. The SM ruled to leave the history. A secret scanner on the remote may flag it.

**N2 (one of my logs is stored gzipped).** H-107's mutant log is in the tree as `runs/h107_mutant_Y10_9a4bcd1f.log.gz`; gunzipped it is identical to mine. My log has one line with trailing whitespace, which the commit hook would rewrite in a plain file. My H-107 record names the log by its plain name.

**N3 (rollback to beta 141c8031, code only; rule 11).**
- No migration and no new column: **the rollback section needs no SET DEFAULT statement.** No schema, stored data, Beat entry or Redis key to undo.
- After a rollback the old log lines return, the pupil's full name in the worker log on every grading among them (H-91), and the scrubber is gone (H-89). Lines the new code wrote stay as written.
- This is my reading of the code, not a tested rollback.

**N4 (deploy and operations).**
- **A process that cannot import the scrubber does not start** (H-89; with Sentry configured, the Sentry hooks module too). Intended, and read from the code, not tested by a failed start.
- The scrubber is on everywhere and off only while tests run; a management command given the word `test` as an argument would run unscrubbed (my H-89 note N3).
- To find a user from a log line, operators use the id.
- Nothing a user sees changes.

**N5 (what the bundle does not cover).**
- **H-89:** what is still printed by the SM's choice (an address whose local part holds "/", "=", "?" or "&" keeps its front; the shapes not recognised), and that the Sentry log-stream hook rests on the author's hand-built test (my H-89 record).
- **A `print()` is not scrubbed:** the six in `assignments/tasks.py` are H-111, open.
- **H-107:** the cause is H-110 (next bundle). Output that does not go through the runner's stream can still be lost on a full pipe, so rule 18 stays.

**N6 (the four documents).** Added at the founder's instruction. I checked that they are the founder's files, unchanged; I did not check their statements against the code. As the package says, the reason-codes reference describes the contract on staging (Epic A), which beta's API does not send yet.

**N7 (merge-down, for 0b's checklist).** H-89's scrubber replaces Epic A's narrower one, with add/add conflicts in the two files and in the Sentry block of the settings (SM ruling). The files H-91 cleaned can leave the epic's log baseline. Epic A's copy of H-108's comment needs the same correction.

**N8 (open rows; none blocks).** H-110 (MEDIUM, the renderer's log pipe; to fix before the renderer reaches main), H-98, H-105, H-106, H-111, H-114, H-115, H-116; H-117 is a product question for the founder. H-113's one open question (whether any deployed environment was ever given the CI workflows' encryption key) is the founder's to answer.

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b7/` (`check.sh`, `check_27b0d2e0.txt`, `scan_all_27b0d2e0.txt`, `scan_all_141c8031.txt`, `masked_lines.py`).
