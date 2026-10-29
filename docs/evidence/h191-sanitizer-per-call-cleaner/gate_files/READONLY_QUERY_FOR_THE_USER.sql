-- H-191: could a saved answer document hold ANOTHER person's text?
-- READ-ONLY. NOT RUN by us. For the founder to run (or not) on the live
-- database (a read replica if there is one), per the rule that production
-- queries are the founder's to run. Written 2026-10-08 from the code, with the
-- default Django table names (students_studentsubmission, users_customuser,
-- assignments_assignment): CHECK THEM before running.
--
-- What a mixed row would look like: a stored `raw_input` document built from the
-- row's own student answers prints that student's own full name in its header.
-- A document that came out of a race can lack the name (it holds someone
-- else's header) or hold another student's. This query lists the first kind: a
-- stored document that does NOT contain its own student's full name.
--
-- It is a CLUE, not proof: it also lists rows whose name was changed after the
-- document was built, and rows where the name has odd spacing or case; it
-- cannot see a document that contains BOTH names. A listed row is to be
-- looked at by a person, never corrected by a script.
SELECT s.id AS submission_id,
       s.assignment_id,
       s.student_id,
       s.graded_at
FROM students_studentsubmission AS s
JOIN users_customuser AS u ON u.id = s.student_id
WHERE s.raw_input IS NOT NULL
  AND s.raw_input <> ''
  AND btrim(u.first_name || ' ' || u.last_name) <> ''
  AND position(lower(btrim(u.first_name || ' ' || u.last_name)) IN lower(s.raw_input)) = 0
ORDER BY s.graded_at DESC NULLS LAST
LIMIT 200;
-- Assignments: I found no fixed marker of its own that every converted assignment
-- document must contain, so no equivalent query is offered for
-- assignments_assignment.raw_input. A comparison of two rows' documents for
-- shared passages would be needed, which is a person's job.
