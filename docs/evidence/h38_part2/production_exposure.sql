-- H-38 production exposure. READ-ONLY. Run each statement inside
--   BEGIN READ ONLY; ... ROLLBACK;
-- Table and column names are the Django defaults; they were syntax-checked
-- against an empty schema, NOT run on production.
--
-- "Removed" = a license allocation that is inactive and the teacher has no
-- active allocation on that same license. remove_teachers is one cause;
-- license cancellation and expiry also leave inactive allocations, and those
-- teachers can hold the same stale access, so they are included on purpose.
-- allocation.updated_at is the LAST change to the row, so it is the removal
-- time only if nothing touched the row afterwards.

-- Q1: removed teachers who still own courses in the school's sessions.
SELECT a.user_id, u.email, ls.school_id,
       MAX(a.updated_at)      AS removed_at_approx,
       COUNT(DISTINCT c.id)   AS school_courses_still_owned
FROM billing_schoolcreditallocation a
JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
JOIN users_customuser u             ON u.id = a.user_id
JOIN classrooms_course c            ON c.teacher_id = a.user_id
JOIN classrooms_session s           ON s.id = c.session_id
                                   AND s.owner_type = 'SCHOOL'
                                   AND s.school_id = ls.school_id
WHERE a.is_active = FALSE
  AND NOT EXISTS (SELECT 1 FROM billing_schoolcreditallocation a2
                  WHERE a2.user_id = a.user_id
                    AND a2.license_subscription_id = a.license_subscription_id
                    AND a2.is_active)
GROUP BY a.user_id, u.email, ls.school_id
ORDER BY removed_at_approx;

-- Q2: rows in those courses that changed AFTER the removal time.
-- Only what carries a timestamp is derivable; see the "not derivable" list.
WITH removed AS (
  SELECT a.user_id, ls.school_id, MAX(a.updated_at) AS removed_at
  FROM billing_schoolcreditallocation a
  JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
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
-- a teacher who is no longer on that school's license.
SELECT st.id AS student_id, st.school_id
FROM users_customuser st
JOIN classrooms_studentcourse sc ON sc.student_id = st.id
JOIN classrooms_course c         ON c.id = sc.course_id
WHERE st.user_type = 'STUDENT' AND st.school_id IS NOT NULL
GROUP BY st.id, st.school_id
HAVING BOOL_AND(NOT EXISTS (
  SELECT 1 FROM billing_schoolcreditallocation a
  JOIN billing_licensesubscription ls ON ls.id = a.license_subscription_id
  WHERE a.user_id = c.teacher_id AND a.is_active
    AND ls.school_id = st.school_id));

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
