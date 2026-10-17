# Decision required: the assignment-list OpenAPI contract

**Status:** OPEN — pinned by tests, deliberately unfixed, awaiting an
API-contract decision.
**Raised by:** Section 4 (assignments) test audit, 2026-09-09.
**Affects:** `GET /api/v1/assignments` — the published OpenAPI schema only.
No runtime behaviour is in question.

---

## 1. Current runtime behaviour (teacher vs student)

`AssignmentViewSet.get_serializer_class()` picks a different serializer per
caller role. This is real, shipped behaviour and is **not** what is under
review:

| Caller | Serializer | Fields returned |
|---|---|---|
| Teacher | `AssignmentListSerializer` | 17: `id`, `course`, `topic`, `title`, `instructions`, `total_points`, `question_count`, `assignment_type`, `status`, `created_at`, `due_date`, `auto_grade_on_due_date`, `extraction_confidence`, `submission_count`, `scheduled_grading_at`, `grading_task_name`, `is_grading_scheduled` |
| Student | `AssignmentListStudentSerializer` | 11: `id`, `title`, `course`, `course_title`, `topic`, `due_date`, `status`, `score`, `total_points`, `grade_letter`, `remaining_attempts` |

The two shapes overlap but neither contains the other. `score`,
`grade_letter`, `remaining_attempts` and `course_title` are student-only;
`submission_count`, `instructions`, `extraction_confidence` and the
grading-schedule fields are teacher-only.

Both are returned under the same paginated envelope, from the same URL,
with the same HTTP status. Nothing in the response body identifies which
shape it is.

## 2. Current generated OpenAPI behaviour

The document published today describes **only the teacher shape**:

```
GET /api/v1/assignments
  200 -> $ref PaginatedAssignmentListList
           results: array of $ref AssignmentList
```

and `AssignmentList` is the 17-field teacher object, `required: ["course"]`.

`StudentAssignmentList` *is* generated as a component (it is referenced
from other endpoints), but nothing connects it to this endpoint's
response.

### Why: the extension has never been active

`assignments/schema.py` defines `PolymorphicAssignmentExtension`, which is
intended to publish exactly this teacher/student split. It has never run.

drf-spectacular registers an `OpenApiSerializerExtension` **when the module
defining it is imported** — subclassing is the registration. Nothing
imports `assignments.schema`. Its only reference anywhere is:

```python
SPECTACULAR_SETTINGS = {
    ...
    "EXTENSIONS": {
        "polymorphic_assignment": "assignments.schema.PolymorphicAssignmentExtension",
    },
}
```

`EXTENSIONS` is not a drf-spectacular setting. Verified against
drf-spectacular 0.28.0's own `DEFAULTS`: the real keys are
`EXTENSIONS_INFO`, `EXTENSIONS_ROOT` and `EXTERNAL_DOCS`, all for OpenAPI
`x-` vendor extensions. Unknown keys are ignored silently, so the entry
does nothing and never has.

**Current state is wrong but harmless**: the schema is silent about the
student shape rather than describing it incorrectly.

## 3. What the intended contract should be

One endpoint returning two documented shapes, with clients able to tell
which they received. The unresolved question is *how* to express that —
see §7.

## 4. Exactly what enabling the extension as-written would expose

Measured, not predicted: the schema was generated twice in separate
processes, once with `import assignments.schema` and once without.

**Today**, `components.AssignmentList`:

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "string", "format": "uuid", "readOnly": true },
    "course": { "type": "string", "format": "uuid" },
    "title": { "type": "string", "nullable": true, "maxLength": 255 },
    "total_points": { "type": "integer", "nullable": true },
    "status": { "$ref": "#/components/schemas/StatusBbeEnum" },
    "...": "12 more fields"
  },
  "required": ["course"]
}
```

**With the extension active**, the same component becomes:

```json
{
  "type": "object",
  "properties": {
    "user_type": { "$ref": "#/components/schemas/AssignmentListUserTypeEnum" }
  },
  "required": ["user_type"],
  "discriminator": {
    "propertyName": "user_type",
    "mapping": {
      "teacher": "#/components/schemas/AssignmentListSerializer",
      "student": "#/components/schemas/AssignmentListStudentSerializer"
    }
  }
}
```

Three consequences, all verified:

1. **All 17 fields disappear.** `map_serializer` *replaces* the generated
   schema, it does not extend it. Field count goes 17 → 1.
2. **`required` flips** from `["course"]` to `["user_type"]`.
3. **Both discriminator targets do not exist.** drf-spectacular strips the
   `Serializer` suffix when naming components, so the generated names are
   `AssignmentList` and `StudentAssignmentList`. Checked against the
   generated document with the extension active:

   ```
   MISSING !!  AssignmentListSerializer
   MISSING !!  AssignmentListStudentSerializer
   EXISTS      AssignmentList
   EXISTS      StudentAssignmentList
   ```

   The discriminator would resolve to nothing.

**Enabling it as written is strictly worse than leaving it off.** It
replaces a working 17-field model with a one-field stub pointing at two
components that were never generated.

## 5. Impact on existing generated clients

The endpoint's `$ref` chain is unchanged, so the *change is invisible in
the path spec* and shows up entirely in the model.

- **TypeScript / openapi-generator / orval:** `AssignmentList` regenerates
  as `{ user_type: ... }`. Every call site touching `assignment.title`,
  `assignment.due_date`, `assignment.submission_count` etc. stops
  compiling. That is the good case — it fails loudly at build time.
- **Swift / Kotlin (mobile):** same, plus a decode failure at runtime for
  any client shipped against the old model if it treats missing required
  fields as fatal.
- **Anything resolving the discriminator** hits two unresolvable `$ref`s.
  Generator behaviour varies from a hard error to silently emitting a
  model with no variants.
- **Untyped consumers** (raw `fetch`, Postman collections) are unaffected:
  the wire format does not change.

## 6. Backward compatibility

**Not backward compatible as written.** Removing 17 documented fields and
changing `required` is a breaking schema change for every generated
client, even though the HTTP responses themselves are byte-identical
before and after.

A *correct* implementation (§7) can be made near-compatible — a
`oneOf`/`anyOf` that still lists the teacher shape keeps existing clients
resolving something usable — but the naive "just add the import" path
cannot.

## 7. Migration and versioning implications

The discriminator approach has a further problem beyond the dangling refs:

**`user_type` is not in the response body.** It is a property of the
*caller*, derived from the authenticated user. OpenAPI requires a
discriminator's `propertyName` to be a property present in the payload, so
even with the refs corrected a client could not discriminate at runtime —
there is nothing in the JSON to switch on.

That leaves three real options:

| Option | Compatibility | Notes |
|---|---|---|
| **A. Leave as-is; document the split in prose** | Fully compatible | Zero risk. Schema stays teacher-shaped; the student shape is described in the endpoint `description`. Honest but not machine-readable. |
| **B. Add `user_type` to both serializers as a real response field, then wire the (corrected) discriminator** | Additive on the wire; breaking for generated models | The only option that makes the discriminator legitimate. Requires a serializer change, so it alters responses, not just docs. |
| **C. Document both shapes as `oneOf` without a discriminator** | Mostly compatible | Machine-readable, no response change, but clients must disambiguate structurally. |

Whichever is chosen, the corrected mapping must reference `AssignmentList`
and `StudentAssignmentList`, and the frontend and mobile clients must be
regenerated in the same change. If clients cannot be regenerated in
lockstep, this needs to land behind a version bump rather than in place.

## 8. Tests required before enabling

Existing cover (`assignments/tests_schema_extension.py`, 9 tests) asserts
the mapping's shape and that nothing registers the extension in
production. Before flipping it on, add:

1. **Ref resolution** — every `$ref` in the generated document resolves to
   a component that exists. This alone would have caught the current bug
   and should be repo-wide, not assignments-only.
2. **Field preservation** — the teacher variant still publishes all 17
   fields after the extension is applied; a snapshot comparison of the
   pre/post component, so a future extension cannot silently empty a model
   again.
3. **Student variant published** — `StudentAssignmentList` is reachable
   from this endpoint's response, not merely present as a component.
4. **Discriminator validity** — if option B: `user_type` is present in
   real teacher and student responses, asserted through the API, not just
   in the schema.
5. **Round-trip** — the generated document validates against the OpenAPI
   3.x meta-schema (e.g. `openapi-spec-validator`), which catches the
   invalid-discriminator class of error generically.
6. **Client-generation smoke test** — run the real generator the frontend
   uses over the produced document in CI and assert it exits clean. This
   is the only check that actually reproduces the failure mode that
   matters.
7. **A registration test that cannot self-contaminate** — note the
   existing suite's trap: importing `assignments.schema` inside a test
   registers the extension for the rest of the process, so any assertion
   about "is it active?" must be static (scan for importers) or run in a
   subprocess.

---

## Recommendation

Take **Option A or C** unless the mobile client specifically needs a
machine-readable discriminator. Option B is the only fully correct
discriminator, but it changes response bodies to serve a documentation
goal, which is a poor trade unless a client is blocked without it.

Do **not** simply add the import: as measured in §4, that ships a strictly
worse schema than today's.
