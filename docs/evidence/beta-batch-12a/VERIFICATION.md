# Verification: beta-batch-12a, Gate 1 DELTA (five rows: batch 12 without H-148) @ 80bd85da

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Date:** 2026-10-07. **Branch:** `task/beta-batch-12a`, frozen at **80bd85da4e1de11425e25aadf4b008257cbc924d**. Base: beta `d7143538` (batch 11 as pushed).

**What this is.** Batch 12a is the alternative to batch 12, prepared on the Senior Manager's order so that either answer of the user about H-148 can be pushed at once. It is the same five merges I read for batch 12 (H-154, H-137, H-147, H-141, H-150) without H-148's merge, with its own backlog commit and its own full run. This record is a delta on my batch 12 record (`docs/evidence/beta-batch-12/VERIFICATION.md` on `task/beta-batch-12`, commit `7ee553b4`; my file `VERIFICATION_beta_batch_12_gate1.md`, sha256 starts e49c1b64926e9149). That record is not in this branch's tree; what I rely on from it is said below and was checked again here where a command could check it.

**Verdict: VERIFIED.** The branch is a fast-forward from the pushed batch 11; it holds five rows, each merged as the commit its verifier verified plus documents only; nothing of H-148 is in it; the one full run of this bundle, read by me from its raw log, is green and holds exactly the test ids I wrote down before the run ended. I ran no test (rule 15). None of the notes blocks the push.

## What I checked again on this branch (git, by reading)
My script `check_12a.sh` (batch 12's `check.sh` with H-148's lines taken out; output kept, `check_80bd85da.txt`).

- **Fast-forward.** `d7143538` is an ancestor of `80bd85da`: 56 commits, 228 files, 21 outside `docs/`.
- **Two direct commits, both documents only** (0 files outside `docs/` each): `2adc1bb3` (the backlog) and `80bd85da` (the full run's record, its packed log, script copy and summary, four files under `docs/evidence/beta-batch-12a/`).
- **Five merges on the first-parent line, the same five commits as in batch 12** (`a60657c5`, `ce1d641f`, `e036e367`, `42b71ffe`, `220f9cd6`), each clean (`git merge-tree --write-tree` of its parents gives its tree) and each with an empty combined diff; `220f9cd6` names `students/serializers.py` as changed by both sides with no hunk of its own.
- **Each row** (H-154 at `c1a62979`, H-137 at `1d47223b`, H-147 at `cc648b44`, H-141 at `44a23f98`, H-150 at `9d0467d3`): the verified commit is an ancestor of the merged tip, the merged tip is in the branch, no commit after the verified one touches a file outside `docs/`.
- **No migration, model, settings, beat schedule or requirements file** differs from the base; no management command is added or changed.
- **My records are in the tip byte for byte:** the 23 files of H-147 and H-141 (three logs after `zcat`) and batch 11's Gate 1 record: 24 of 24 identical.

## Nothing of H-148 is in it
- H-148's tip `b8098a65` is not an ancestor of `80bd85da`.
- No file outside `docs/` differs between `220f9cd6` and `80bd85da`, so the code and tests are exactly those of the fifth merge.
- No file outside `docs/` at the tip names `fill_empty_name`, `typed_name_used` or `tests_support_add_by_email`; no path under `docs/evidence/` is H-148's folder.
- The three classrooms files H-148 shares with H-147 (`classrooms/serializers.py`, `classrooms/views.py`, `classrooms/tests_course_payload_student_exposure.py`) equal H-147's tip `2507838b` at this tip.
- So add by email is as on beta today: an email alone is accepted. The two billing test modules of my H-148 finding are in their old form here, which is right for this tree, and they are in the full run and green (81 ids).

## What I rely on from my batch 12 record, unchanged here
- **`students/serializers.py`** (merged by git from H-141 and H-150 at `220f9cd6`): each side's change carried whole and as the same text, the two changes in different places, no way found for one to change what the other shows. The file at this tip is the file at `220f9cd6`.
- **Rule 17's addendum** (each mutation battery is against its module's last test change) for the five rows.
- The three rows of Verifier 2's were not re-verified by me, there or here.

## The one full run of this bundle (0b's; rule 15, not repeated by me)
Record: `docs/evidence/beta-batch-12a/FULL_SUITE.md`. Read by me from the committed, packed log:

- **The log:** `strict_b12a.log.xz` unpacks to 8,018,157 bytes, sha256 `3cb3163810743d8de6aa591e49a5cc3df3dff320b23c261bb85effb50fc95b6e`, as the record says.
- **One Ran line:** `Ran 6047 tests in 492.106s`, `OK (skipped=28)`. 0 lines beginning `FAIL:` or `ERROR:`.
- **The run's tip** is `2adc1bb3`; `80bd85da` adds four files under `docs/evidence/beta-batch-12a/` and nothing else.
- **Test ids, against what I wrote beforehand.** At 17:09:14, while the run was going and before I had any result, I wrote in my state note that it should hold 6047 distinct ids: batch 12's 6086 less the 39 of H-148's three new modules (25 `classrooms.tests_teacher_names_student_on_add`, 12 `users.tests_student_cannot_name_themselves`, 2 `classrooms.tests_staff_invitations_promise_no_password_change`), and I made that list (`b12a_expected.ids`). The log's 6047 ids are that list exactly (compared byte for byte). Against batch 11's 5915: 2 gone (the two of batch 12: one renamed by H-147, one of H-154's row), 134 new.
- **Per app,** counted by me: ai_processor 852, assignments 663, AutoGrader 669, billing 2109, classrooms 434, dashboard 270, students 396, users 654; they sum to 6047 and equal the record's table.
- **Skips: 28,** by the reasons printed, the same as batch 12's: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, 2 that cannot fork inside a parallel worker.
- **Rule 20:** `AutoGrader.tests_cache_bespoke_1114` is in the run (24 ids, green).
- **What ran beside the suite** is told in 0b's record. To it I add my own, all on git objects but for two files of the installed error-reporting library that I read with grep and sed; no file of any worktree was read: before the suite began (17:08:39 to about 17:09:10, during the script's type-check step) I pre-read this branch with two-tree diffs, one `git ls-tree -r` of its tip and two `git grep` commands limited to `classrooms/`; during the suite, at 17:09:21, the blob compare of my H-153 record (nine `git show`), and between about 17:09:37 and 17:11, for the Senior Manager's order to pre-read H-167, two-tree diffs, single-file `git show` reads, one `git ls-tree -r`, one `git grep` over one commit's Python files and one over two named files. I took the grant's "reading and two-tree diffs" to cover them; the tree listing and the `git grep` are of the kind 0b lists for Verifier 2, so I list mine.

## The credential pattern scan (every value masked)
On 0b's GRANT in the delta request I ran the tool of record (`~/Documents/Projects/GAP-0b-runs/credscan/credscan.py`, sha256 starts bdbd2e3d5b0e4b70) with `--all --lines` on `80bd85da` (17:20:07 to 17:20:45, exit 0, 3810 files, archives opened) and compared it with my scan of the base `d7143538` of 16:31 and with my scan of the six-row tip.

- **Addresses with a password part:** 5 rows, the same 5 as on the base. None new.
- **The same value in encoded and decoded form:** 0 groups.
- **Literal assignment rows outside test files: 193 on the tip, 178 on the base; 15 new, none gone, all 15 under `docs/`.** 13 of them are rows I read masked for batch 12 (9 words in prose in the backlog and in the evidence of H-137, H-141, H-150 and H-154; 4 in two of H-154's logs, the known warning line). The 2 that are new to this branch are in this batch's own full-run log, lines 75056 and 75061, read masked: the same known line (a warning that a fetch of a test host's address was blocked; the word before the colon is the last word of that address's path, the "value" the test host's name, 13 characters). Not credentials.
- **The 12 rows of H-148's evidence** that the six-row tip had (among them the five generated test passwords of my batch 12 note N3) are not in this tree.

## The backlog commit and the package
- **`2adc1bb3`** changes `docs/HARDENING_BACKLOG.md` only. Against batch 12's backlog (`00d0b699`) it differs in 12 rows, read by me word by word: "batch 12" becomes "batch 12a" in the five closed rows; **H-147's row now says the ten keys rightly** (my batch 12 note N1: ten of the teacher's fields of a nested assignment, of which only the count of submissions was about classmates; four keys are new); **H-148's row reads "Built and verified; NOT in batch 12a: held for the frontend, with H-152 and H-153 (batch 12b)"**; H-152 and H-153 read as built, gated and verified (H-153 "1a VERIFIED-WITH-NOTES at `05c83b0d`", with the neutral refusal's cost, the log line only and that no page calls the route yet); H-167 is rewritten as the HIGH row it now is; H-168 gains the rename-and-add case; H-169 and H-170 are new.
- **The package draft** (`~/Documents/Projects/GAP-beta-batch-12a-push-package.md`, as it stood at 17:19): it says in its first lines that this is the alternative to batch 12 and that H-148, H-152 and H-153 are not in it; "Nothing in this batch breaks a request the current pages send. Add by email is unchanged."; H-147's ten keys by name; H-110, H-121 and H-127 under "Before promotion to main"; no migration. Its figures agree with mine (56 commits, 228 files, 21 outside `docs/`, 6047, 132 more than batch 11, 39 fewer than batch 12, 28 skips).

## Notes (not blocking)
- **N1. Which of the two goes out is the user's decision, not this record's.** Both tips are verified by me: the six rows at `44f23e06` (record at `7ee553b4`) and these five at `80bd85da`. Only one of them is to be pushed.
- **N2. "Nothing breaks a request the current pages send"** is true of the routes' requests as I read them. Two rows still change what a student's page RECEIVES (H-147: a nested assignment's `status` changes meaning and ten keys go; H-141: a null score in the upload answer), and nobody on the team can read the frontend. The package's frontend section says both.
- **N3. Without H-148 a student added by email still has no name** and no teacher can give one (H-148, H-152 and H-153 are held together as batch 12b).
- **N4. The open rows** of the five are as my row records and batch 12's record say (H-162, H-163, H-6's line).
- **N5. What I did not do:** no test run; no re-reading of Verifier 2's three rows; no reading of the scan's non-literal lines one by one; nothing observed on a live or staging service.

## Files of this check (in `~/Documents/Projects/GAP-1a-scratch/gate1-b12/`, sha256 prefixes; not committed)
- `check_12a.sh` and its output `check_80bd85da.txt`
- `scan_all_80bd85da.txt` a6f6f6052f8e9489 (base: `scan_all_d7143538.txt` f81c7e40ad453c3b)
- `b12a_expected.ids` 218ae53b14319e69 (written 17:09:14) and `b12au` (the log's ids; identical)
