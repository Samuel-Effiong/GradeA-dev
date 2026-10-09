# v2 pre-read of H-208 (d5), tip d067e6d6, read-only, no run

Five places (all reached by describe_error_for_log): safe_delay; launch/mark_processing_task_failure; cancel (revoke); normalize (state read); grading follow-up dispatch.

Another road to error reporting (Sentry LoggingIntegration event_level=ERROR stays; the line is still an ERROR event, now a message with class (+frames), no exception object):
- mark_processing_task_failure: callers in assignments/tasks.py + classrooms/tasks.py all `raise` after it in their except-Exception bodies (grade_engine_async 630, upload 913 `raise exc`); refusal returns (UPLOAD_REFUSALS, teacher_may_reach) pass error None / a refusal -> not this line. So a real fault still reaches Celery/Sentry as an exception. launch_processing_task: non-broker `raise`; broker -> ProcessingTemporarilyUnavailable (503 APIException), reported only by the log event.
- safe_delay, cancel, normalize: swallow broker errors only; the log event is the only report (class alone) = the design.
- follow-up dispatch: swallows ALL Exception (grade committed); the log event (class + frames) is the ONLY report for a non-broker fault.
Design fact: BROKER_UNAVAILABLE_ERRORS = redis ConnectionError/TimeoutError, kombu OperationalError (not Django's), BUILTIN ConnectionError, BUILTIN TimeoutError. requests.exceptions.ConnectionError is NOT the builtin. Confirmed by reading dispatch.py. Caveat: _chain follows __cause__ or __context__, so ANY error raised while a broker error is being handled is named by the broker class alone, no frames.

Assertion reading (empty/missing-value satisfiable?):
- H1 (helper, broker alone): `.py` not in text; error never raised -> no traceback -> satisfiable by empty. Known; fix pending.
- L2, L4, billing TimeoutError test: errors raised and caught, traceback exists, text asserted non-empty first -> not empty-satisfiable.
- L1, C1, S1, D1: text non-empty + id + class asserted before the absences. OK.
- F1: "ConnectionError" is asserted on `everything` (all loggers), not on services_text. The launcher's own line (task_tracking) carries ConnectionError, so presence is satisfiable by ANOTHER line. d5's own expectation M2 -> {L4} only confirms F1 stays green when the services line names the wrapper. Weak PRESENCE.
  (The submission-id presence is NOT weak: M11 killed by F1 only.)
- Frames-absence for a broker class is asserted only in L2, L4, H1, billing timeout; C1/S1/D1/F1 rely on the shared helper.
