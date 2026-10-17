-- READ-ONLY. Detection for package F6: trial buckets whose remainder was
-- written off more than once.
--
-- A trial bucket expires at trial_end, so cleanup_expired_credit_buckets
-- (05:00) often expires it first and writes an EXPIRE row. expire_trial
-- then wrote a second EXPIRE row for the same remainder, because it didn't
-- skip a bucket that was already processed. Two overlapping
-- expire_active_trials runs did the same. Balances were not affected (the
-- bucket was already spent or expired); the ledger overstates the credits
-- that expired, by overstated_expired_credits_raw (raw units;
-- display = raw / 1000).
--
-- Output: ids, counts, credit totals and timestamps only. No emails.
SELECT
    l.bucket_id,
    b.wallet_id,
    COUNT(*) AS expire_rows,
    SUM(l.amount) AS expired_credits_raw_as_recorded,
    SUM(l.amount) - (ARRAY_AGG(l.amount ORDER BY l.created_at, l.id))[1]
        AS overstated_expired_credits_raw,
    MIN(l.created_at) AS first_expire_at,
    MAX(l.created_at) AS last_expire_at
FROM billing_creditledger AS l
JOIN billing_creditbucket AS b ON b.id = l.bucket_id
WHERE l.ledger_type = 'EXPIRE'
  AND b.bucket_type = 'TRIAL'
GROUP BY l.bucket_id, b.wallet_id
HAVING COUNT(*) > 1
ORDER BY first_expire_at;
