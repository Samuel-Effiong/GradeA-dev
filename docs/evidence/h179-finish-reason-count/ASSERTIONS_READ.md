# H-179: every assertion read against the real value (2026-10-08, BEFORE any run)

Ruling (Senior Manager): option A, log only. One WARNING line per cut-off
reply from `execute_graded_task`, right after the reply is read. Fixed prefix
"AI reply cut off by the length limit:", then task_type, the model the reply
names, finish_reason. No prompt or reply text, no user or submission id, no
e-mail. getattr with None; the read never raises; a non-string reason is
absent. The "existing metrics module" of the row's brief exists only on the
next-stage line; on beta there is none. The retry loops are not changed.

| Test | What it inspects (form) | Red under |
|---|---|---|
| logged once with its task type | the cut-off records of ONE call (a sentinel line keeps assertLogs from failing when nothing is logged and is filtered by prefix): count 1, `levelno` WARNING, `record.args == ("extract_answer", "model-x", "length")` (the record is read, not searched as text) | K1, K7 (and the base) |
| exactly the three fields | `len(record.args) == 3` and three "=" in the formatted message | K1 (and the base) |
| "stop" is not logged | the filtered records are `[]` | K2 |
| no reason at all | `[]` for finish_reason None | none (a control: only the base-green set) |
| object without the attribute | `[]` and the call returns the same reply object (a SimpleNamespace without finish_reason) | K3 |
| no choices | `[]` for `choices=[]` | K4 |
| a Mock for the reason | `[]`; first asserts the reason really IS a MagicMock | none (control) |
| model that is not text | `record.args == (task, None, "length")` | K1, K5 (and the base) |
| unmetered super-admin branch | `[]` for a 'length' reply to a super admin with both flags | K6 |

Which tests no mutant decides (controls, not evidence): "no reason at all" and
"a Mock for the reason" (they also pass on the base). They hold that nothing
is logged for those inputs; K2 would be needed to turn them red only if the
reason check accepted None or a Mock, which no mutant does.

Stated limits: (1) the log line reaches whatever the log handlers send; I did
not look at the error tracker's configuration, so "the same line reaches the
error tracker as a warning" is the Senior Manager's statement, not mine.
(2) Only the first choice's reason is read. (3) A cut-off reply is still
charged and returned exactly as before; nothing about retries changes.
