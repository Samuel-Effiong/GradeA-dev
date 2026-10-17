# H-247: who a student is when a teacher adds them

Code commit: `fb7bc8bd` on `task/h247-student-identity` (from beta `ec482691`).

## The owner's rules (2026-10-16)

| Situation | Result |
|---|---|
| Same email, any teacher allowed to add it | The same student. Never a second account |
| Same email, a different name typed | Refused. Upper/lower case and extra spaces don't count; a different or missing middle name does |
| No email | A name is unique within one course only. The same name in another course is a new student; the same name in this course is that student |
| A school's student, a teacher from another school | Refused |
| A school's student, an individual teacher who never taught them | Refused |
| A school's student, a teacher who already teaches them (rule (b)) | Allowed, in a course the teacher can still reach (H-38) |
| An individual teacher's student, added by a school teacher | Allowed, the same student, from then on associated with that school |
| Students already joined across courses | Left as they are |

## What changed

- `classrooms/services/enrollment.py`
  - `apply_typed_name`: one name per email. It fills a nameless account (H-148), and otherwise refuses a different name with `NAME_MISMATCH_MESSAGE`. The message is neutral: it never prints the stored name, so the add forms can't be used to look up the name behind an address.
  - `already_teaches`: rule (b), using `teacher_course_access_q`.
  - The name check runs **after** the staff, school and disabled-account gates. A refusal for another school's student never mentions the name (mutant M10).
- `classrooms/serializers.py`: the direct add applies the same rule, and now fills a nameless account like the single add does.
- `classrooms/services/roster_import.py`
  - `_find_existing_student_by_name` is removed.
  - A no-email row whose name is already in the course is skipped as `ROW_ALREADY_ENROLLED`. Any other no-email row is a new student.
  - The name refusal is the new row code `ROW_NAME_MISMATCH`.
- `ROW_NAME_MISMATCH` has been added to `audit/enums.py`, `AutoGrader/reason_codes.py` and `docs/GRADE_A_PLUS_REASON_CODES.md` (64 codes, 45 user-facing). Its wording was proposed by the SM and **approved by the owner on 2026-10-17**, including the choice not to show the stored name.

## Tests

- `classrooms/tests_h247_student_identity.py`: 33 tests covering every row of the table above, on the single add, the direct add and the import.
- 14 older tests changed:
  - **Tests of rules the owner replaced.** H-148's "a stored name stands and the typed one is ignored" now expects a refusal. The no-email cross-course reuse (tenancy, H-38 positive control and S7d clash) now expects a new student, or a skip within the same course.
  - **Tests not about names** now type the account's own name. The shared helper `tests_support_add_by_email.add_by_email` sends an existing account's stored name.
- Enrolment-related modules before the gate: 701 tests OK.

## Verification

- **Mutation: 15/15 killed** (`mutation_fb7bc8bd.json`, runner `mutate.py.txt`).
  - Every restore came from `git show fb7bc8bd:<path>`, and its sha256 matched.
  - `git status --porcelain` was empty afterwards.
  - No "too many clients" appeared in the log.
- **Final gate** on `fb7bc8bd`: a detached worktree, a fresh database `test_h247_gate` with no `--keepdb`, run under `systemd-inhibit`.
  - pre-commit, migration safety and makemigrations all exited 0.
  - Result: **8060 tests, OK (skipped=30)**.
  - Fingerprint identical before and after. Excerpt in `final_gate_fb7bc8bd_excerpt.txt`.
  - Afterwards `pg_database` had no `test_h247_gate*` rows, with 0 connections.

## Approval

- The owner approved the refusal wording on 2026-10-17, including the choice not to show the stored name.
- After the gate, the only change outside `docs/` is one comment in `AutoGrader/tests_reason_codes.py` recording that approval. `AutoGrader.tests_reason_codes` was re-run on it.
