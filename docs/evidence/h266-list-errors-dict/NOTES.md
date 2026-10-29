# H-266 notes

Security Engineer (db).

- **The row:** H-266, severity Low, no owner. Mentioned in H-263's backlog entry.
- **Source:** SM 2026-10-27 (move the frontend to the dict-by-index error shape).
- **Branch:** `task/h266-list-errors-dict`, from `aede70b4` (H-263's gated tip, DRF 3.18.2).
  Batch 31. Private venv (DRF 3.18.2).

## What the read found (at `aede70b4`)

`REST_FRAMEWORK["LIST_SERIALIZER_ERRORS_AS_DICT"]` is `False` (`AutoGrader/settings.py:1207`).
DRF 3.18.0 changed `many=True` validation errors from a list (`[{}, {"points": [...]}]`) to
a dict by failing index (`{1: {"points": [...]}}`). H-263 kept the list shape; DRF 3.20 drops
the switch and warns `RemovedInDRF320Warning` on every failing list input.

The four inputs affected:
- `rubric` (`assignments/serializers.py:75`, nested `many=True` field on `QuestionSerializer`)
- `tasks` (`assignments/serializers.py:914`, nested `many=True` field)
- `session_breakdown` (`classrooms/serializers.py:1206`, nested `many=True` field)
- bulk topic create (`classrooms/views.py:1897`, top-level `TopicSerializer(many=True)`)

The renderer (`users/renderers.py:164`) wraps error data into `field_errors` only when it is a
`Mapping`. With the list shape, top-level `many=True` errors (like bulk topic create) are a
list, so `field_errors` is absent. With the dict shape, it is a Mapping, so `field_errors`
appears. For nested `many=True` fields (rubric, tasks, session_breakdown), the top-level
error is already a dict keyed by field name, so `field_errors` is always present — only the
inner structure changes.

The `message` field in the error envelope is byte-identical in both shapes: `flatten_errors`
overwrites the prefix at each nested dict level, so the intermediate index key does not appear.

## What changes

- `AutoGrader/settings.py`: `LIST_SERIALIZER_ERRORS_AS_DICT` from `False` to `True`.
  One-line change. The comment is updated.
- `assignments/tests_h263_list_error_shape.py`: tests flipped to assert the dict-by-index
  shape. New tests: `test_the_dict_shape_is_a_mapping` (the renderer wraps it in
  `field_errors`) and `test_the_error_message_is_byte_identical_to_the_list_shape` (the
  `message` field doesn't change).

## Not changed here

- The renderer (`users/renderers.py`): unchanged. The `isinstance(data, Mapping)` check now
  matches for top-level `many=True` errors, so `field_errors` appears where it didn't. This is
  desired behaviour, not a code change.
- The four serializers: unchanged. DRF's setting controls the shape.
- The frontend: not in this repo. The dict shape is ready for it to consume.

## Tests

### tests_h263_list_error_shape.py: RubricErrorShapeTests (4 tests)

- `test_only_the_failing_item_appears_keyed_by_index`: `[valid, invalid]` → `{1: {...}}`.
- `test_the_failing_item_is_reported_at_its_own_index`: `[invalid, valid]` → `{0: {...}}`.
- `test_the_dict_shape_is_a_mapping`: the error dict is a `Mapping` (the renderer wraps it).
- `test_the_error_message_is_byte_identical_to_the_list_shape`: `flatten_errors` produces
  "Points: A valid number is required." — same as with the list shape.

### tests_h263_list_error_shape.py: TopicBulkCreateErrorShapeTests (2 tests, API level)

Added by the SM on 2026-10-29 before any gate run: the bulk topic create is the one input
where `field_errors` appears for the first time, and the scope names it, so it gets a test
(declared gaps are not accepted for the paths a row exists to protect).

- `test_bulk_topic_errors_are_field_errors_keyed_by_index`: `POST /course/<id>/topics` with
  `["Algebra", ""]` → 400, `error.field_errors == {"1": {"name": ["This field may not be blank."]}}`.
- `test_bulk_topic_error_message_is_unchanged`: same request, `message` is
  "Name: This field may not be blank." in both shapes (guard, green at the base).

## Expected reds against `aede70b4` (written before the gate)

At the base `LIST_SERIALIZER_ERRORS_AS_DICT` is `False`, so the errors are list-shaped.
Call-site revert: only `AutoGrader/settings.py` is taken back to the base.

"Ran 6 tests", `FAILED (failures=4)`, 4 blocks (4 FAIL, 0 ERROR):

1. `test_only_the_failing_item_appears_keyed_by_index`: FAIL, the errors are a list. Fragment "!=".
2. `test_the_failing_item_is_reported_at_its_own_index`: FAIL, same reason. Fragment "!=".
3. `test_the_dict_shape_is_a_mapping`: FAIL, a list is not a Mapping. Fragment "is not an instance of".
4. `test_bulk_topic_errors_are_field_errors_keyed_by_index`: FAIL, the top-level error is a
   list so the renderer sends `error: {}`. Fragment "not found in".

Green at the base (guards: the `message` text must not change):
- `test_the_error_message_is_byte_identical_to_the_list_shape`
- `test_bulk_topic_error_message_is_unchanged`

## Near suite + guards (written before the gate)

`assignments.tests_h263_list_error_shape users.tests_renderers classrooms.tests_tenancy_and_roster`
plus the 20 guard modules in the script: **Ran 332**, OK. The count is from a DiscoverRunner id
list at the tip (332 ids, 332 unique), not grep.

## Expected mutant results (written before the gate)

1 mutant on `AutoGrader/settings.py`, run against the whole module.
Expected: 1/1 KILLED, 0 SURVIVED, 0 BROKEN.

| ID | What | Expected status | Expected failing tests |
|----|------|-----------------|------------------------|
| M1 | flip True back to False | KILLED | keyed_by_index ("!="), at_its_own_index ("!="), is_a_mapping ("is not an instance of"), bulk_topic_errors_are_field_errors_keyed_by_index ("not found in") |

Security Engineer (db); pack completed and run by the Senior Manager on 2026-10-29 (the
Security Engineer's session ended).
