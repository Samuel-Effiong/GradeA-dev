# Grade A Plus: How Students Are Added and Recognised

The rules that decide who a student is when a teacher adds them to a class, and why each rule exists.

**Status:** in force on beta from 17 October 2026 (H-247). Rules 15 to 17 were already in place before that.
**Applies to:** every way a teacher adds a student. That means adding one student by email, adding a student directly (with or without an email), and uploading a class list.

## Contents

- [The idea in one paragraph](#the-idea-in-one-paragraph)
- [1. Students with a real email address](#1-students-with-a-real-email-address)
- [2. Students without an email address](#2-students-without-an-email-address)
- [3. School boundaries](#3-school-boundaries)
- [4. Rules that already existed](#4-rules-that-already-existed)
- [5. How this works with individual and school licences](#5-how-this-works-with-individual-and-school-licences)
- [6. Quick reference](#6-quick-reference)
- [7. What the teacher sees when a student is refused](#7-what-the-teacher-sees-when-a-student-is-refused)

## The idea in one paragraph

An **email address** is the only thing that reliably tells us two entries are the same child. So with an email, the system joins everything to one student, and that student has one name. **Without** an email, a name alone can't prove two entries are the same child, so the system never joins them across classes. On top of that, **schools keep their students**: other schools and outside teachers can't pull them in, except a teacher who already teaches that student.

## 1. Students with a real email address

### Rule 1. One email address is one student, whichever teacher adds it

Teacher A and Teacher B both add `jane@gmail.com`. They get **the same Jane**, never a second account.

**Why:** an email belongs to one real person. With one account, Jane has one login and sees all her classes in one place. Duplicate accounts split her work and confuse everyone.

### Rule 2. That student has one name everywhere

If a teacher types a different name for an email that's already in use, the student **is not added**. Nothing is saved, enrolled or emailed.

**Why:** if each teacher could name her differently, she would be "Jane Smith" in one class and "Janet Smyth" in another. Papers are matched to students by name, so a wrong name means papers go to the wrong place.

### Rule 3. Capitals and extra spaces don't count, but a middle name does

- "jane  SMITH" is the same as "Jane Smith", so the student is accepted. The spelling already on the account is kept.
- "Jane Ann Smith" is **not** the same as "Jane Smith", so the student is refused. A missing middle name also counts as different.

**Why:** a capital letter or an extra space is obviously a typing difference, and refusing it would only annoy teachers. A different middle name might mean a different child, so the system doesn't guess.

### Rule 4. The refusal never shows the name already on the account

**Why:** if it did, anyone could type any email address and find out the name of the child it belongs to. That is a privacy leak about children. If a teacher has the name wrong, they ask the student or family how it's written on the account.

### Rule 5. An account with no name yet takes the teacher's typed name

**Why:** students can't name themselves. Without this, nobody could ever give that account a name, and its papers would match nobody.

## 2. Students without an email address

Some students, such as young children, have no email. The system gives each of them a hidden internal address of its own, which nobody ever types.

### Rule 6. A name only has to be unique within one class

"Jane Smith" in English and "Jane Smith" in History are **two separate students**, even if the same teacher adds both.

**Why:** with no email there is no way to know they're the same child. Wrongly joining two different children mixes up their grades and records, which is far worse than having two separate entries.

### Rule 7. Inside one class, the same name means the same student

- Uploading the class list again **skips** students who are already in the class ("already in this course"). It doesn't create duplicates.
- Two students in one class can't have exactly the same name.

**Why:** papers are matched to students by name. Two identical names in one class would make it impossible to tell whose paper is whose. To tell two such students apart, the teacher adds a middle name or initial.

### Rule 8. Students already joined across classes are left as they are

Before this change, uploading a class list could join a student without email to the same-named student in another of that teacher's classes. Those existing accounts are **not split**.

**Why:** splitting them would mean moving grades and submissions between accounts, which is risky. The new rule applies to new additions only.

## 3. School boundaries

A student **belongs to a school** once any teacher of that school teaches them, or once a school teacher creates them.

### Rule 9. A school's student can't be added by a teacher from another school

**Why:** each school's student records belong to that school. Another school must not be able to pull them into its classes.

### Rule 10. A school's student can't be added by an individual teacher who has never taught them

**Why:** it's the same protection, applied to teachers outside any school. School accounts and personal accounts are kept apart.

### Rule 11. A teacher who already teaches the student keeps access

If a tutor taught Jane first and her school added her later, **the tutor can still add Jane to new classes**.

**Why:** the school rule is meant to keep strangers out, not to lock out a teacher the student already has.

### Rule 12. "Already teaches" only counts classes the teacher can still reach

A teacher who has left a school doesn't keep that school's pupils through the school's old classes.

**Why:** otherwise leaving a school would let a teacher take its students' records with them.

### Rule 13. A school teacher may add a student who so far only had individual teachers

It's the same student. From then on, that student belongs to the school.

**Why:** a child with a private tutor can still join their own school with their one email address, without needing a second address. Nobody's records are exposed, because each teacher only ever sees their own classes.

### Rule 14. A refusal is always just a refusal

When a rule says no, the system never quietly creates a second account to get around it.

**Why:** a hidden duplicate account would undo every rule above.

## 4. Rules that already existed

### Rule 15. A staff email can't be added as a student

This covers a teacher's, school administrator's or platform administrator's email.

**Why:** a staff account must never be turned into a student account. The refusal doesn't say what kind of account it is, so it can't be used to find out who works where.

### Rule 16. A switched-off account isn't switched back on by a teacher adding it

**Why:** someone switched it off on purpose, and a teacher's add shouldn't undo that.

### Rule 17. A hidden internal address can never be typed in by a teacher

These are the addresses ending in `@student.local`, which belong to students without an email.

**Why:** they are internal keys, not mailboxes. Accepting one could attach someone else's student to the wrong class.

## 5. How this works with individual and school licences

### The two kinds of teacher

| | Individual licence | School licence |
|---|---|---|
| Who | A teacher with their own personal subscription | A teacher given a seat by their school's administrator |
| Belongs to a school? | No | Yes, while they hold the seat |
| Their students | Belong to no school, unless a school also teaches them | Belong to that school |

Which school a student belongs to always comes from **the teachers who teach them**. It is never typed in.

### Individual licence teachers

| Situation | Result | Rule |
|---|---|---|
| Adds a new student with an email | A new student is created | 1 |
| Adds a student another individual teacher already has | The **same** student, with the same name | 1, 2 |
| Adds a student who belongs to a school, and has never taught them | **Refused** | 10 |
| Adds a student they already teach, who has since joined a school | **Allowed**, the same student | 11 |
| Adds "Jane Smith" without an email to a second class | A **new**, separate student | 6 |

### School licence teachers

| Situation | Result | Rule |
|---|---|---|
| Adds a student with an email who is new to the platform | A new student, belonging to the school | 1, 13 |
| Adds a student another teacher in the **same school** already has | The **same** student | 1 |
| Adds a student who belongs to **another school** | **Refused**, unless they already teach that student | 9, 11 |
| Adds a student who so far only had individual teachers | **Allowed**, the same student, who now belongs to the school | 13 |
| Adds "Jane Smith" without an email to a second class | A **new**, separate student | 6 |

### When a teacher moves between licences

**An individual teacher joins a school licence.**
- Their students start counting as that school's students, because they are now taught by a school teacher.
- The teacher keeps their own classes.
- A teacher who still has an active personal subscription, or who already belongs to another school, can't be given a seat. That is checked by the licence, before any of the student rules apply.

**A teacher is removed from a school licence.**
- They become an individual teacher again.
- They lose access to the school's classes, and with them the school's students (rule 12).
- Students they created while in the school stay the school's students.
- They keep their own personal classes. For a student who is in both a personal class of theirs and the school, rule 11 still lets them add that student to new personal classes.

**A teacher who belongs to a school creates a personal class.**
- Students in it still count as the school's, because the teacher is a school teacher.

## 6. Quick reference

| Situation | Result |
|---|---|
| Same email, any teacher allowed to add it | The **same** student, never a second account |
| Same email, a **different name** typed | **Refused**. Nothing changes and the stored name isn't shown |
| Same email, the same name in other capitals or spacing | Accepted, and the stored spelling is kept |
| No email, same name, **another** class | A **new**, separate student |
| No email, same name, the **same** class | Skipped as "already in this course" |
| A school's student, a teacher from **another school** | **Refused** |
| A school's student, an **individual teacher** who has never taught them | **Refused** |
| A school's student, a teacher who **already teaches** them | **Allowed** |
| An individual teacher's student, a **school teacher** adds them | **Allowed**, and the student now belongs to that school |
| A staff email | **Refused** |
| A switched-off account | **Refused**, and it stays switched off |
| Students already joined across classes | Left as they are |

## 7. What the teacher sees when a student is refused

| Case | Adding one student | Class-list upload (one row) |
|---|---|---|
| Different name for an existing email | "This email already belongs to a student with a different name. Use the name on the student's account, or check the email address." | `ROW_NAME_MISMATCH`: "Row N: this email already belongs to a student with a different name." |
| Another school's student | "This account cannot be added to this school. If you believe this is a mistake, contact your school administrator." | `ROW_OTHER_SCHOOL` |
| Staff email | "This email can't be added as a student." | `ROW_STAFF_EMAIL` |
| Switched-off account | "This student's account is disabled. Contact support if they should have access." | `ROW_ACCOUNT_DISABLED` |
| Already in the class | "Student is already enrolled in this course." | `ROW_ALREADY_ENROLLED` (skipped) |
| Two different students with the same name in one class | "A student with the exact name … is already enrolled in this course." | `ROW_NAME_CLASH` |

The exact wording of every row code is in [GRADE_A_PLUS_REASON_CODES.md](GRADE_A_PLUS_REASON_CODES.md). The technical record and test evidence for these rules are in [evidence/h247-student-identity/EVIDENCE.md](evidence/h247-student-identity/EVIDENCE.md).
