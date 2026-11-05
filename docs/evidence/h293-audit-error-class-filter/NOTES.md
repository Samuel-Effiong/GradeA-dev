# H-293 notes

- Change: `error_class` ChoiceFilter on _BaseAuditEventFilter (so both the
  super-admin and school-admin endpoints get it; school-admin scope is
  untouched) + OpenAPI parameter with enum from ErrorClass.values.
- E1 red: first draft was 5/7 failing, not the 6 expected:
  test_combines_with_outcome passed unfixed because school_id+outcome
  already isolated the row. Strengthened (USER failure added in school B);
  re-run 6/7 failing as expected (red.txt).
- E2 green 7/7 (green.txt). E3 plant (declaration + Meta entry removed -
  either alone still yields a filter) killed, 5 failures; restored, sha256
  match (plant.txt). E4 near: audit Ran 446 OK (skipped=2) (near.txt).
- No migration. No index on error_class (outcome has none either); fine at
  current volume, combine with time_from/time_to on large ranges.
