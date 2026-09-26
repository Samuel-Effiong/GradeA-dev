# audit

Phase 2 Epic A, task T1: the audit event table and the one way to write to it.
Nothing calls `emit()` yet; call sites arrive with the epics that own each
action.

## Writing an event

```python
from audit.emitter import emit
from audit.enums import AuditAction

emit(
    AuditAction.ASSIGNMENT_COPY,
    actor=request.user,
    request=request,
    target_type="Assignment",
    target_id=assignment.id,
    metadata={"item_count": 3},
)
```

`emit()` returns the saved `AuditEvent`, or `None` if the write was rejected or
could not be stored. **It never raises into the user's action** (FR-A-11): a
rejected write, a broken table and an unreachable database all become one error
line in the log. Pass `strict=True` in a call-site test to make a rejected write
raise `AuditValidationError` so the mistake is visible.

A failure is `outcome=AuditOutcome.FAILURE` (or `DENIED`) **plus** an
`error_class`. A success has no error class. Anything else is rejected.

## What it does for you

| Rule | Where |
|---|---|
| Who acted, and under which licence, is stored as a value, never a foreign key. Deleting a user or school cannot erase or change the trail | `models.py` |
| Rows cannot be edited or deleted. Only `source_ip` and `user_agent` may be blanked later, by the retention sweep | `models.py` (`mutable_fields`) |
| A student's email, address and browser are never stored, in the emitter and again in a database check | `emitter.py`, `models.py` |
| `metadata`, `before` and `after` accept only allow-listed keys with short scalar values | `metadata.py` |
| The trace id is minted by the server. The client's `X-Request-ID` is kept as untrusted `client_correlation_id` | `context.py`, `emitter.py` |
| The retention class is derived from the action and cannot be lowered by a caller | `enums.py`, `emitter.py` |
| The source address is the one the edge proxy saw (`NUM_PROXIES`), not one the client claims | `emitter.py` |

## Changing the vocabulary

* **A new action:** add a member to `AuditAction` (`enums.py`). It is code, not a
  migration. Add it to `STUDENT_RECORD_ACTIONS` if it always touches a student's
  record. `tests_enums.py` pins both lists, so update it in the same change.
* **A new metadata key:** add it to `ALLOWED_KEYS` (`metadata.py`) in a reviewed
  change; that list is the review point. A key naming a person or their work
  (name, email, answer, text, title ...) is refused by a test.

## Not here yet (T2 and later)

Reason-code catalogue, error taxonomy, carrying the trace id across Celery,
the retention sweep, the query API and metrics. `emit()` writes inside the
caller's transaction, so emit a failure event after the failing transaction has
rolled back.

Run: `python manage.py test audit`
