# Verifier 2: merge-down b16, VERIFIED at 86dcfdc6

Written 2026-10-09 (slot 20:07:22 to 20:08:57 WAT, Release Engineer's GRANT, load 3.55).
Tip 86dcfdc6 = 72bce4fd + 52941a6f + dfee4d19 (tests and docs only after 72bce4fd). Merge commit read: 5798be46
(parents d2ad0405 line, 1edd5c92 beta, base 8567a7a0), post-merge docs 0ff08520, 607e41b2.

## Reading (git objects only)
- side_check.py: 498 beta-only and 1155 line-only files: every merged blob equals its side (0 differ); nothing touched outside either set.
- 15 files changed on both sides: 7 equal git's own three-way result; 8 had conflicts (14 blocks; the note says 13, a miscount). Every block read; each resolution is what the note says.
- pyflakes on the 15 merged blobs: clean. No migration. Post-merge commits docs only.
- Signature/arity search by my own reading: one beta signature change (formatted_grade_async + result_stamp, defaulted; both view callers pass it); the door's callees match their calls; no line-added log site carries a broker error with text.
- pinscan.py: the roads pin's own KINDS/ROADS/skip rules applied to the blobs of 72bce4fd (users/admin.py ACTIVE_TRUE found 0, pinned 1) and dfee4d19 (no difference).

## Run (vf_b16_all_run.sh 86dcfdc6; mutants vf_b16_mutants.py, vf_pin_mutants.py; expected sets written before the run, inside the scripts)
Baselines: Ran 54 OK (users.tests_auth_audit_doors, students.tests_batch_item_results, students.tests_upload_credit_door); Ran 10 OK (users.tests_roads_that_sign_in_or_activate).

| Mutant | Result | Failing set | Fragment |
|---|---|---|---|
| B1 invitation refusal untagged | killed, EXACT set; written fragment WRONG | new invitation test + test_school_admin_bad_code_survives_the_rollback | wrote "0 != 1"; got 'INVALID_REQUEST' != 'INVALID_CODE' (the generic request audit still writes one event) |
| B2 invitation without admin-power refusal | killed EXACT | new invitation test | 200 != 400 |
| B3 Google drops the account | killed EXACT | new Google test | None != UUID |
| B4 Google reason changed | killed EXACT | new Google test | reason mismatch |
| B5 Google without H-203 refusal | killed EXACT | new Google test | 200 != 400 |
| B6 door refuses when it cannot estimate | killed (MUST) | both ATooLarge tests + test_a_file_the_door_cannot_read_is_left_to_the_task | 402 != 202 / 402 != 200 |
| B7 no per-item 413 | killed (MUST) | ATooLarge batch test + ThirtyFilesTwelveFailures | is not None |
| B8 door asked after the session | killed EXACT | TeacherBatchDoorTest poor-teacher test | 1 != 0 |
| P1-P5 (ed's pin mutants) | 5/5 killed EXACT | as ed wrote | all fragments present |

Restores: 13 of 13 sha256-ok against the commit blobs; scratch tree clean and at 86dcfdc6 after the run.
Rule 22 note: B1's failure is for the property the test pins (the reason code), not for the mechanism I wrote; counted killed, not re-run.

## Not covered / named
- Four of the pin's new tests have no mutant of their own (ed named it).
- reset_password's H-164/H-202 refusals write no failed-sign-in event (older than this merge).
- ClawbackRaceTests deadlock (H-222, d5) is outside the judged modules.
- The module gate, the full run and pre-commit are 0b's.
