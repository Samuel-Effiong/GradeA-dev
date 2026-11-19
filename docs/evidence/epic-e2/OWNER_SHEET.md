# Grade categories and assignment copy: what teachers and admins will see

Nothing changes until we switch it on. A course that never sets up categories keeps exactly the grades it has today.

## 1. Teachers

- **Grade categories.**
  - A teacher can split a course's grade into named categories, such as Exams 50%, Homework 30% and Participation 20%. The weights must add up to 100%.
  - Each assignment goes in a category. Inside a category, an assignment can count more than others: a final exam with weight 2 counts twice as much as a midterm with weight 1.
  - Before saving new weights, the teacher can **preview** how the class and each student's grade would change.
  - A student's grade can be shown **broken down by category**.
- **Excused work.** A teacher can mark a student "excused" for an assignment. It then doesn't count for or against that student.
- **Copy.**
  - A teacher can copy one assignment, or up to 50 at once, into any of their courses, including a course in another term.
  - The copy brings the questions, answer key, instructions, points, category and tags. It never brings students' work, grades or feedback, and it costs no credits.
  - The copy arrives as a **draft**, so students are told only when the teacher publishes it.
  - In a bulk copy, if some items fail, the rest still copy and the teacher sees which ones failed and why.

## 2. School admins

- They can make **category templates** for the school, such as "Standard secondary", that teachers can pick when they create a course. Teachers can also make their own.

## 3. Questions for you

- **Q1.** Some dashboard "average grade" figures count a 5-point quiz the same as a 100-point exam, while the final grade weighs by points. On 6 Nov you chose to make them one formula in this piece of work. Those dashboard numbers will move once, for every course, when we switch it on. Go ahead? *Suggested: yes.*
- **Q2.** Should missing work (published, past due, not handed in) count as 0? *Suggested: the teacher chooses per course, and the default is "doesn't count", which is how it works today.*
  **Decided 2026-11-19 (option 1):** missing work counts as 0 by default for every course, new and existing. A teacher can still switch it off per course.
- **Q3.** In a course with categories, what about an assignment with no category? *Suggested: it doesn't count, the teacher sees a warning naming it, and new assignments in that course must pick a category.*
- **Q4.** If a category has nothing graded yet, for example no exam given so far, what should happen? *Suggested: its weight is shared among the other categories, so students aren't marked down for work that hasn't happened.*
- **Q5.** Should school admins make school templates now, or teachers only? *Suggested: both now.*
- **Q6.** Should a copy bring the due date? *Suggested: no. The teacher sets a new one; the copy arrives as a draft.*
- **Q7.** There are old, unused category names in the database that belong to no course. *Suggested: delete them.*
- **Q8.** The requirement says a copy carries the "grade level", but assignments have no grade level today. *Suggested: don't add one now.*
- **Q9.** Bulk copy limit. *Suggested: 50 at a time.*

## 4. New wording to approve (drafts)

| # | Where | Text |
|---|---|---|
| W1 | Error | "Category weights must add up to 100%. Right now they add up to {total}%." |
| W2 | Error | "This course already has a category called "{name}"." |
| W3 | Error | "Each category weight must be between 0% and 100%." |
| W4 | Error | "An assignment's weight in its category must be more than 0." |
| W5 | Warning (Q3) | "{n} assignments have no category, so they don't count toward the grade. Choose a category for each one." |
| W6 | Error (Q3) | "Choose a category for this assignment. This course uses grade categories." |
| W7 | Copy title, same course | "{title} (copy)", then "{title} (copy 2)" |
| W8 | Copy failure reason | "This assignment can't be copied. It may have been deleted." |
| W9 | Copy failure reason | "You can't copy into this course." |
| W10 | Error | "You can copy up to 50 assignments at a time." |
| W11 | Error | "This student isn't in this course." |
| W12 | Error | "You already have a template called "{name}"." |
| W13 | Grading rules (shown to teachers) | "Excused work doesn't count toward the grade." / "Missing work counts as 0." or "Missing work doesn't count until it's handed in." / "Work that isn't graded yet doesn't count." |
| A1 | Activity log label | "Grade categories changed" |
| A2 | Activity log label | "Category template changed" |
| A3 | Activity log label | "Work excused" |
| A4 | Activity log label | "Excuse removed" |
