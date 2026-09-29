-- H-38 production exposure. READ-ONLY by construction: the whole file runs
-- inside one read-only transaction that is rolled back, so no statement can
-- write even if edited by mistake. Output is ids, types, timestamps and
-- counts only: no emails or names (founder rule for production output).
-- Run with, e.g.:  psql "$DATABASE_URL" -f production_exposure.sql
-- Table and column names are the Django defaults. The 2026-09-28 revision
-- (read-only wrapper, email dropped, lapsed licences excluded) was executed
-- in full against the migrated schema of task/teacher-removal@3161413 (an
-- empty test database): BEGIN READ ONLY, Q1, Q2, Q3 and ROLLBACK all ran
-- without error. That proves syntax and every table/column name; it says
-- nothing about the result on production data. NOT yet run on production.
--
-- "Removed" = the teacher's allocation on a licence is inactive, they have
-- no active allocation on that licence, AND THE LICENCE ITSELF IS STILL
-- ACTIVE. Before the fix, remove_teachers left user.school set, so in stored
-- data a removed teacher looks exactly like a teacher whose licence lapsed.
-- The licence's own state is what separates them:
--   * licence active, this allocation inactive  -> removed (in scope);
--   * licence cancelled or expired              -> the teacher is still a
--     school member, and reaching their own school's courses is INTENDED
--     behaviour (SM/product ruling 2026-09-28), so it is excluded.
-- Known under-count: a teacher removed while the licence was active, whose
-- licence later lapsed, is excluded too. Stored data cannot tell that case
-- apart from an ordinary lapse.
-- allocation.updated_at is the LAST change to the row, so it is the removal
-- time only if nothing touched the row afterwards.

BEGIN TRANSACTION READ ONLY;

-- Q1: removed teachers who still own courses in the school's sessions.
SELECT a.user_id, ls.school_id,
       MAX(a.updated_at)      AS removed_at_approx,
       COUNT(DISTINCT c.id)   AS school_courses_still_owned
FROM billing_schoolcreditallocation a
JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
                                   AND ls.is_active
JOIN classrooms_course c            ON c.teacher_id = a.user_id
JOIN classrooms_session s           ON s.id = c.session_id
                                   AND s.owner_type = 'SCHOOL'
                                   AND s.school_id = ls.school_id
WHERE a.is_active = FALSE
  AND NOT EXISTS (SELECT 1 FROM billing_schoolcreditallocation a2
                  WHERE a2.user_id = a.user_id
                    AND a2.license_subscription_id = a.license_subscription_id
                    AND a2.is_active)
GROUP BY a.user_id, ls.school_id
ORDER BY removed_at_approx;

-- Q2: rows in those courses that changed AFTER the removal time.
-- Only what carries a timestamp is derivable; see the "not derivable" list.
WITH removed AS (
  SELECT a.user_id, ls.school_id, MAX(a.updated_at) AS removed_at
  FROM billing_schoolcreditallocation a
  JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
                                     AND ls.is_active
  WHERE a.is_active = FALSE
    AND NOT EXISTS (SELECT 1 FROM billing_schoolcreditallocation a2
                    WHERE a2.user_id = a.user_id
                      AND a2.license_subscription_id = a.license_subscription_id
                      AND a2.is_active)
  GROUP BY a.user_id, ls.school_id
), theirs AS (
  SELECT c.id AS course_id, r.user_id, r.removed_at
  FROM removed r
  JOIN classrooms_course c  ON c.teacher_id = r.user_id
  JOIN classrooms_session s ON s.id = c.session_id
                           AND s.owner_type = 'SCHOOL'
                           AND s.school_id = r.school_id
)
SELECT 'assignment_updated' AS what, t.user_id, COUNT(*) AS n
  FROM theirs t JOIN assignments_assignment x ON x.course_id = t.course_id
  WHERE x.updated_at > t.removed_at GROUP BY t.user_id
UNION ALL
SELECT 'assignment_created', t.user_id, COUNT(*)
  FROM theirs t JOIN assignments_assignment x ON x.course_id = t.course_id
  WHERE x.created_at > t.removed_at GROUP BY t.user_id
UNION ALL
SELECT 'submission_graded_or_regraded', t.user_id, COUNT(*)
  FROM theirs t
  JOIN assignments_assignment x ON x.course_id = t.course_id
  JOIN students_studentsubmission sub ON sub.assignment_id = x.id
  WHERE sub.graded_at > t.removed_at OR sub.regraded_at > t.removed_at
  GROUP BY t.user_id
UNION ALL
SELECT 'topic_created', t.user_id, COUNT(*)
  FROM theirs t JOIN classrooms_topic x ON x.course_id = t.course_id
  WHERE x.created_at > t.removed_at GROUP BY t.user_id
UNION ALL
SELECT 'enrollment_created', t.user_id, COUNT(*)
  FROM theirs t JOIN classrooms_studentcourse x ON x.course_id = t.course_id
  WHERE x.created_at > t.removed_at GROUP BY t.user_id;

-- Q3: reverse leak. Students stamped with a school whose only link to it is
-- a teacher who is no longer on that school's licence. Schools with no active
-- licence are excluded for the same reason as above (a lapse is intended).
SELECT st.id AS student_id, st.school_id
FROM users_customuser st
JOIN classrooms_studentcourse sc ON sc.student_id = st.id
JOIN classrooms_course c         ON c.id = sc.course_id
WHERE st.user_type = 'STUDENT' AND st.school_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM billing_licensesubscription ls0
              WHERE ls0.school_id = st.school_id AND ls0.is_active)
GROUP BY st.id, st.school_id
HAVING BOOL_AND(NOT EXISTS (
  SELECT 1 FROM billing_schoolcreditallocation a
  JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
  WHERE a.user_id = c.teacher_id AND a.is_active
    AND ls.school_id = st.school_id));

ROLLBACK;

-- NOT DERIVABLE from stored data:
--  * hard deletes (assignments, submissions, topics, enrollments): the rows
--    are gone and there is no audit or soft-delete table; the count of rows
--    is the only trace, compared against any backup.
--  * edits to topics and enrollments: neither has an updated_at column.
--  * WHO made a change: no actor column exists on these tables, so a change
--    after removal is only attributable by elimination (the owner was the
--    removed teacher, but a school admin or a system task could also touch
--    a row; assignments.updated_at in particular moves on system writes).
--  * publish-all-grades: not yet confirmed to write anything.
