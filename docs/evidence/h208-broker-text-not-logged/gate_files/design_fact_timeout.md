# H-208 design fact: the builtin TimeoutError (and ConnectionError) is a broker-unavailable error everywhere

`AutoGrader/dispatch.py` BROKER_UNAVAILABLE_ERRORS = (redis ConnectionError, redis TimeoutError, kombu OperationalError, builtin ConnectionError, builtin TimeoutError).
Every use of the tuple (safe_delay, launch_processing_task, cancel_processing_task revoke, normalize_processing_task_status, describe_error_for_log) therefore treats a NON-broker builtin TimeoutError/ConnectionError the same way. In H-208 this means such an error is logged by CLASS ALONE, without frames.

By reading (Python 3.12.10 in the project venv: socket.timeout, asyncio.TimeoutError and concurrent.futures.TimeoutError ARE the builtin TimeoutError):
- openai APITimeoutError, httpx timeouts and requests.Timeout are NOT the builtin: they keep frames.
- Can raise the builtin from non-broker code on the paths that reach mark_processing_task_failure (any exception from a task body): a raw socket timeout (e.g. an SMTP or other socket-level call), asyncio.wait_for / future.result(timeout=) in a worker. In the repo, assignments/pdf_renderer.py catches the future timeout and re-raises PDFRenderError, so it does not escape as the builtin. billing/live_qa/concurrency.py builds a TimeoutError but is a QA tool, not on these paths. No other repo code raises it (grep for TimeoutError, wait_for, settimeout over non-test .py files).
- Not checked: what third-party libraries raise internally without a repo line naming it.
- Effect on the user: none new. AutoGrader/error_messages.py already classes builtin TimeoutError as "grading timed out" for the user text; only the operator's log line loses frames for it.
