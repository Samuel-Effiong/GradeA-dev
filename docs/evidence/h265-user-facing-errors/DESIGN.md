# H-265 design note (SM, 2026-10-29, before any code)

## The defect
About 20 views answer `{"detail": str(exc)}` for any caught `ValueError`. The services' own
`ValueError`s are mostly sentences written for the user, but a library `ValueError` raised inside
the same `try` (Stripe SDK, Django, `Decimal`, `int()`) would be shown verbatim.

## Survey (base a3f5a6a1)
- Sites still quoting `str(exc)` of a bare `ValueError`: license_views.py 326, 669, 722, 789, 865,
  900, 928, 964, 1002; views.py 978, 1178, 1295, 1343; license_overage_offline_views.py 115, 143;
  dashboard/views.py 2515, 3586; serializers.py 2183; qa_time_travel.py 1018;
  views_admin_credits.py 143.
- Already safe (catch a domain class or show fixed text): serializers.py 334 (PlanAssignmentRefused),
  2232 (LicenseRequestError); license_views.py 95/97 (LicenceStripeChangeNotRecorded's fixed text);
  qa_time_travel.py 903 (Stripe `user_message`, QA-only tool; out of scope, noted).
- No service raise embeds a caught exception's text (`raise ValueError(f"...{exc}")`: none). The leak
  is only library ValueErrors.
- About 123 bare `raise ValueError(` are reachable from those calls (name-based call graph, an
  over-count). Not all are written for users: e.g. `Plan {name} has no stripe_price_id configured`
  is an operator setup fault.

## The cure
1. One domain class, `billing.errors.BillingRequestError(ValueError)`: "a billing request the
   caller can fix; its text is written to be shown". A ValueError subclass, so every existing
   `except ValueError` and `assertRaises(ValueError)` keeps working (same as LicenseRequestError).
   Registered in `AutoGrader.error_messages._user_facing_exception_types()`.
   The dashboard parameter helpers get `dashboard.views.QueryParamError(ValueError)`, also registered.
2. Every raise reachable from the sites is read and classified:
   - a sentence written for the user -> `BillingRequestError` (or `LicenseRequestError` in licence code);
   - operator/config/programming faults stay a bare `ValueError` and now get the fallback.
   The classification is a table in NOTES.md, one line per raise.
3. Each site: `{"detail": describe_user_error(exc, fallback_message=<operation sentence>)}`, same
   status code as today. The log line carries the exception class only (no text, H-260).
   No status-code changes in this row.

## Tests (one per site, two kinds)
- library ValueError: the called service is patched to raise `ValueError("sk_live_LEAK internal")`;
  the response detail is the site's fallback and "sk_live_LEAK" appears nowhere in the body.
  Red at the base: the leak text is the detail.
- user sentence: the service raises its real domain error; the detail is that sentence (green at
  the base: a guard that the cure does not hide real refusals).
Plus existing tests asserting service messages through these views: run as the near suite; a
message that turns into the fallback there is a misclassified raise.

## Not in scope
Status codes (400 vs 500 for programming faults); qa_time_travel.py 903; non-billing apps.
