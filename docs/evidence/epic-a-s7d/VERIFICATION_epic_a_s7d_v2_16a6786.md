# Verification: Epic A S7d, QA error catalogue sections B–F (ed)

- **Branch:** task/epic-a-s7d at **16a6786**. Production is final at 41ba153; 3f3ba90 is a test-only fix; the rest is evidence.
- **Verifier:** v2 (independent), 2026-10-01.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base update 7087ad7: `git show --remerge-diff` | Empty (a clean auto-merge) |
| Added-line survival (`vf_merge_survival.py 7087ad7`) | base ca35971, sides 2e29ac9 / 830bf8d, **0 lines lost** |
| Epic movement since (830bf8d..ba165b4) | Docs only |
| H-71 copy (ee6f4b2) | `classrooms/services/__init__.py` matches cf4e405 (empty diff). The enrollment.py hunks are identical to H-71's own commit 5a5e4c3 |
| H-78 copy (0dde18e, 023a51a) | `_get_or_invite_teacher` and `_invite_and_enroll_one_teacher` are byte-identical to 8a31d19 (AST segments compared). The test module is byte-identical to 45c36f2. The source branch has only that test fix plus records since 8a31d19, so no re-sync is needed |
| Rule 14 (ed's new tests) | No MagicMock. The stand-ins are `Sent` recorders and `new=` functions. `get_throttles` is patched with `return_value=[]`, which is never rendered |
| Read-through against PREP_s7d.md and SM Q1–Q7 | Every item met (E: the savepoint wraps the account, seat and invite; removal answers every bad id alike; D: row numbers, duplicates, email check before any write; F: `skipped` on both publish-all paths). The variant and remediation arguments keep the 4-tuple for every existing error |

## ed's gates (read, not repeated: rule 15)
- 509 OK at 023a51a.
- 245 OK at 41ba153.
- Combined regression at 41ba153: 4634/4635. The one failure was S6b's photo-as-PDF test expecting the old code. It was fixed test-only at 3f3ba90 and re-run in the ruled form: 22 OK.
- Mutation: 12 + 14 + 13 + 7 + 3 = **49/49 killed** by named tests, no survivors.

## v2 probe (0b's grant, 6G, rule 16 prefix, timeout -k 60 1800, scratch worktree at 16a6786, DB test_vf2_s1)
**16 tests: 15 OK, 1 FAIL in v2's own probe.** Log: runs/s7d_16a6786.log.

- **D-ROW:** with a header, rows read [1, 3, 4] around a blank line; without one, [1, 3]. Row text starts "Row 4:".
- **D-DUP:**
  - A repeat of a row that itself failed (ROW_NAME_INVALID) gets ROW_DUPLICATE with first_row=1.
  - An email repeat is matched case- and space-insensitively; a name repeat case-insensitively.
  - Exactly one email is sent.
- **D-EMAIL:** "abc" gives ROW_EMAIL_INVALID. No account is created and nothing is sent.
- **D-NAME:** on the emailed path, 151 characters, a 1-character first name and a 1-character last name all give ROW_NAME_INVALID, and nothing is sent.
- **D-STAFF:** teacher, school admin and super admin rows are identical apart from the reference. The params are `{row}` only, with no role word.
- **D-ISO:** a real Postgres error (`SELECT 1/0`) in row 2 gives the generic ROW_FAILED, and rows 1 and 3 are enrolled.
- **D-EMPTY:** blank rows only, no header, give 400 ROSTER_EMPTY.
- **D-COUNT:** total 5 (one blank). Added 1, failed 1, skipped 2, so success + failure + skipped = 4.
- **E-ISO (a) DB error and (b) a raise after the account exists:** t1 and t3 are added and invited. t2 leaves no account, no allocation and no email. TEACHER_ADD_FAILED carries no exception text.
- **E-ORDER:** another school's teacher who has their own subscription gets TEACHER_IN_OTHER_SCHOOL only. There is no school name and no billing word.
- **E-RM:** six bad ids give identical TEACHER_NOT_ON_LICENCE items. They are: an unknown id, another school's teacher, a student, a teacher not on the licence, "not-a-uuid" and a JSON object. The good id is removed and listed in `removed`.
- **E-ONE:** with one seat left and two teachers added, the answer is 400 LICENCE_SEATS_EXCEEDED, "1 seat left, but you're adding 2 teachers (1 of 2 in use)". The params are ints and `availability` is not among them.
- **F-NONE:** with nothing graded, the answer is 200 "No graded submissions found to publish.". `skipped` lists both submissions (never graded, and graded_at without a score) with SUBMISSION_NOT_GRADED, and nothing is published.
- **D-SIZE: FAIL, a probe defect.** The probe set `read` on an InMemoryUploadedFile, where `read` is a property with no setter. The AttributeError inside the patched `parse_roster` gave the 500, before S7d's code ran.
  - Not repeated. The behaviour is pinned by ed's `test_the_size_is_checked_before_the_file_is_read`, whose input file's `read()` raises.
  - Mutant D11 (the size checked after the read) is killed by 3 tests, including `test_too_large_is_a_413_with_int_sizes`.
  - The code reads `validate_upload_size` before `.read()`.
- **B-RENEW / F-HALF:** not written as probes. They are ed's `test_the_renew_door_keeps_its_own_answer` (429, no REGISTRATION_PAUSED, the renew text) and `test_every_not_fully_graded_state_is_refused_with_the_code` (all three half-graded states). v2 read both.

## Notes (none blocks)
1. **E-LOG: the address in the logs on a successful add.** Adding a teacher logs their address in 5 lines:
   - users.signals: post-save, and the trial skip;
   - license_service: "Enrolled teacher …", "Created MONTHLY credit bucket … for teacher …", "Queued teacher invitation email to … for school …".

   All five are older than S7d and outside condition (b), which covers the email codes' refusals. ed's log test covers refused teachers only, and that holds. **For the SM:** a backlog row if success logs should be ids-only too. `_enroll_teacher_internal`'s unreachable subscription refusal also logs the address.
2. **The SYNC_ONLY_EMAIL_CODES guard** catches a direct `raise` that names the code, and a task module naming the codes or their builders. It doesn't catch an error bound to a variable and raised later, or a task reaching a builder through a helper module. These are bounded limits, like H-73's.
3. **Removal:** a non-UUID or a JSON-object id answers like an unknown id, as required. `remediation` is null for TEACHER_NOT_ON_LICENCE (the approved "none").
4. **The probe defect above:** disclosed; it is not an S7d result.
