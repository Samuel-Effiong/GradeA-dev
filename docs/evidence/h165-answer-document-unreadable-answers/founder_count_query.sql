-- READ ONLY. For the user to run on production (and on beta) if they
-- choose; the Senior Manager tells them. Nobody on the team runs it, and
-- it has NOT been tried against any database: read it before running.
-- It changes nothing: two SELECTs. It returns COUNTS by month only: no
-- name, no id, no answer text, no score.
--
-- Question (H-165, written 2026-10-07): how many stored submissions hold
-- answers in a shape the answer document builder cannot print, and how
-- many of those are not yet released to the student?
--
-- Why: the builder expects `answers` to be a list of objects (one per
-- question). On any other shape that is not empty it raises. By reading
-- the code: on beta today neither writer can store such a shape; the
-- live service's "edit a submission by text" path saves the answers
-- before the document is built, so it could have. For such a row, on
-- beta's code: the student's read of the paper before release answers
-- an error on every read, a read by anyone answers an error if the
-- stored document is empty, and grading fails. H-165 cures it.
--
-- The shapes, as the builder treats them:
--   harmless: a list of objects; an empty list; an empty object {}; an
--             empty string; null, false or 0 (the builder skips them);
--   it raises on: a non-empty object, a non-empty string, a number other
--             than 0, true, or a list holding anything that is not an
--             object (a string, a number, a list, null).
--
-- What it can NOT tell:
--   * whether anybody met an error because of such a row;
--   * anything about rows since deleted.
--
-- Table: Django's default name for students.StudentSubmission. If it is
-- named otherwise in this database the query fails with "relation does
-- not exist" and changes nothing.
WITH shaped AS (
    SELECT
        s.submission_date,
        s.is_published,
        s.graded_at,
        (s.raw_input IS NULL OR s.raw_input = '')           AS no_stored_document,
        jsonb_typeof(s.answers::jsonb)                      AS kind,
        CASE
            WHEN jsonb_typeof(s.answers::jsonb) = 'array' THEN EXISTS (
                SELECT 1
                FROM jsonb_array_elements(s.answers::jsonb) AS e(value)
                WHERE jsonb_typeof(e.value) <> 'object'
            )
            WHEN jsonb_typeof(s.answers::jsonb) = 'object'
                THEN s.answers::jsonb <> '{}'::jsonb
            WHEN jsonb_typeof(s.answers::jsonb) = 'string'
                THEN s.answers::jsonb <> '""'::jsonb
            WHEN jsonb_typeof(s.answers::jsonb) = 'number'
                THEN s.answers::jsonb <> '0'::jsonb
            WHEN jsonb_typeof(s.answers::jsonb) = 'boolean'
                THEN s.answers::jsonb = 'true'::jsonb
            ELSE false
        END                                                 AS builder_raises
    FROM students_studentsubmission s
)
SELECT
    to_char(date_trunc('month', submission_date), 'YYYY-MM') AS month_submitted,
    count(*)                                                  AS submissions,
    count(*) FILTER (WHERE kind <> 'array')                   AS answers_not_a_list,
    count(*) FILTER (WHERE builder_raises)                    AS builder_would_raise,
    count(*) FILTER (WHERE builder_raises AND NOT is_published)
                                                              AS of_those_not_released,
    count(*) FILTER (WHERE builder_raises AND graded_at IS NULL)
                                                              AS of_those_not_graded,
    count(*) FILTER (WHERE builder_raises AND no_stored_document)
                                                              AS of_those_with_no_stored_document
FROM shaped
GROUP BY 1
ORDER BY 1;

-- A second SELECT, for scale: the kinds of value stored, all months.
SELECT
    jsonb_typeof(answers::jsonb) AS kind_of_answers,
    count(*)                     AS submissions
FROM students_studentsubmission
GROUP BY 1
ORDER BY 2 DESC;
