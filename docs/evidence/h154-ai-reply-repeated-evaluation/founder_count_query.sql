-- NOT NEEDED: the user's word of 7 October 2026, passed on by the Senior
-- Manager, is that no existing grade is above the maximum and no
-- assignment has a repeated question number. That is the user's statement;
-- the team has run no query. This file is kept, not asked to be run.
--
-- READ ONLY. For the founder to run, on beta and on production, if they
-- choose; nobody on the team has run it anywhere. It changes nothing: one
-- SELECT. It returns COUNTS by month only: no name, no id, no score.
--
-- Question (H-154): how many saved grades have a score above the paper's
-- maximum, or a percentage above 100?
--
-- Why: until H-154 an AI grading reply that repeated a question, or held
-- an evaluation for a question that does not exist, had those points
-- added into the score more than once.
--
-- What it can NOT tell:
--   * A repeat that left the total at or under the maximum (the student
--     had lost marks elsewhere) is not found by this: the row looks
--     ordinary. So the counts are a LOWER bound on affected grades.
--   * A teacher may type a manual grade; the last two columns split the
--     rows whose score is still the AI's own (score = ai_score) from rows
--     a teacher changed afterwards.
--   * max_points is the maximum saved with the grade; rows with no
--     maximum or a maximum of zero are counted apart, not judged.
SELECT
    to_char(date_trunc('month', graded_at), 'YYYY-MM')        AS month,
    count(*)                                                   AS graded_rows,
    count(*) FILTER (WHERE max_points IS NULL OR max_points <= 0)
                                                               AS no_usable_maximum,
    count(*) FILTER (WHERE max_points > 0 AND score > max_points)
                                                               AS score_above_maximum,
    count(*) FILTER (WHERE score_percentage > 100)             AS percentage_above_100,
    count(*) FILTER (WHERE max_points > 0 AND score > max_points
                       AND ai_score IS NOT NULL AND score = ai_score)
                                                               AS above_maximum_and_still_the_ai_score,
    count(*) FILTER (WHERE max_points > 0 AND score > max_points
                       AND (ai_score IS NULL OR score <> ai_score))
                                                               AS above_maximum_and_changed_by_a_teacher
FROM students_studentsubmission
WHERE graded_at IS NOT NULL
  AND score IS NOT NULL
GROUP BY 1
ORDER BY 1;
