-- H-256: per-call durations from the usage record (ai_processor_aicallrecord, H-201), by task type and outcome.
-- READ-ONLY, numbers only. Not run for H-256: the local database has no such table and the beta login in .env
-- is refused. For whoever holds a working login, to check the 300 s default against real traffic.
BEGIN TRANSACTION READ ONLY;
SELECT task_type, outcome, count(*) AS n,
       round((percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) / 1000.0)::numeric, 1) AS p50_s,
       round((percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) / 1000.0)::numeric, 1) AS p95_s,
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) / 1000.0)::numeric, 1) AS p99_s,
       round(max(duration_ms) / 1000.0, 1) AS max_s,
       min(created_at)::date AS first_day, max(created_at)::date AS last_day
FROM ai_processor_aicallrecord
WHERE duration_ms IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2;
-- Calls that already ended in a timeout or a dropped connection.
SELECT task_type, error_class, count(*) FROM ai_processor_aicallrecord
WHERE error_class IN ('timeout', 'connection') GROUP BY 1, 2 ORDER BY 1, 2;
ROLLBACK;
