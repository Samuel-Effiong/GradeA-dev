-- READ-ONLY. Detection for package F6, MOST IMPORTANT: paying customers
-- whose subscription the trial-expiry task may have switched off.
--
-- The race: expire_active_trials read a trial, then Stripe's trial-end
-- payment (or a mid-trial checkout) converted it to paid ON THE SAME ROW,
-- then the task expired its stale copy. That wrote is_active = false on a
-- subscription the customer had just paid for. It left the rest of the
-- conversion in place: is_trial = false, the paid billing period, and
-- stripe_status = 'ACTIVE'.
--
-- So a candidate is a subscription that is inactive now, had a PAID trial
-- conversion charge recorded at or before its last update, was switched off
-- inside the period it paid for, and was not cancelled at Stripe
-- (customer.subscription.deleted sets stripe_status = 'CANCELED').
-- A renewal deactivates the old row only once its period has ended, so
-- renewals are not reported.
--
-- updated_at is the row's last save, which for these rows is normally the
-- deactivation. Review each row by hand before acting:
--   * trial_expired_ledger_at: the "Free trial expired" EXPIRE row that
--     expire_trial wrote for this subscription after the conversion. When
--     present, it corroborates the race (it's absent when the trial bucket
--     was empty or already processed).
--   * newer_subscription_created_at: the customer subscribed again later
--     (still harmed: they paid twice).
--   * cancelled_at: a scheduled cancellation, which still means they paid
--     through the period.
--
-- Output: ids and timestamps only. No emails.
SELECT
    s.id AS subscription_id,
    s.user_id,
    t.id AS conversion_transaction_id,
    t.created_at AS converted_at,
    s.updated_at AS deactivated_at,
    s.billing_cycle_end AS paid_through,
    s.stripe_status,
    s.cancelled_at,
    (
        SELECT MIN(l.created_at)
        FROM billing_creditledger AS l
        WHERE l.ledger_type = 'EXPIRE'
          AND l.metadata ->> 'subscription_id' = s.id::text
          AND l.reference LIKE 'Free trial expired%'
          AND l.created_at >= t.created_at
    ) AS trial_expired_ledger_at,
    (
        SELECT MIN(n.created_at)
        FROM billing_usersubscription AS n
        WHERE n.user_id = s.user_id
          AND n.created_at > s.created_at
    ) AS newer_subscription_created_at
FROM billing_usersubscription AS s
JOIN billing_billingtransaction AS t
    ON t.user_subscription_id = s.id
   AND t.transaction_type = 'INDIVIDUAL_TRIAL_CONVERSION_CHARGE'
   AND t.status = 'PAID'
WHERE s.is_active = false
  AND s.is_trial = false
  AND t.created_at <= s.updated_at
  AND s.updated_at < s.billing_cycle_end
  AND s.stripe_status IS DISTINCT FROM 'CANCELED'
ORDER BY s.updated_at;
