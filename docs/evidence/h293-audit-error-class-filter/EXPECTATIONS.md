# H-293 expectations (written before the code)

Owner ask 2026-11-05: filter audit events by error class.

- E1 red: audit/tests_h293_error_class_filter.py, 7 tests; before the fix
  6 fail (unknown param is ignored, so filters return every row and the
  bad value gets 200; schema lacks the param), 1 passes (no-filter).
- E2 green: all 7 pass after adding `error_class` ChoiceFilter to
  _BaseAuditEventFilter (both endpoints) + OpenAPI parameter.
- E3 plant: drop the filter declaration -> filter tests fail.
- E4 near run: audit app, OK.
- No migration (filter only; no new index - outcome has none either).
