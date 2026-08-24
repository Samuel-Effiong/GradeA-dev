"""
Server-side PDF rendering via a warm, headless Chromium instance.

Why this exists: WeasyPrint (the previous PDF pipeline for assignment
downloads) has no LaTeX/math renderer at all, so an assignment option like
"$x = 5$" printed as literal text - dollar signs and all - instead of a
typeset formula. Chromium can typeset it correctly via KaTeX (vendored
locally in assignments/vendor/katex/, not fetched from a CDN, so a render
never depends on outbound network access or a third party being up), but
WeasyPrint cannot run the JavaScript KaTeX needs, so switching the whole
renderer to Chromium's own print-to-PDF was the only way to get real math
typesetting without giving up selectable/copyable text (unlike rasterizing
formulas to images, which Chromium's PDF text layer is not).

Concurrency model: Playwright's *sync* API is only safe to use from the
single thread that created its objects. This app serves requests with
gunicorn's gthread worker class (multiple threads per worker process), so
naively calling Playwright from whichever thread happens to handle a
download request would be unsafe. Instead, _ChromiumRenderWorker starts one
dedicated background thread per process that owns the one warm Chromium
instance for that process's entire lifetime; every other thread submits a
render job through a queue and blocks on that job's own event, so all
actual Playwright calls happen on the single owning thread regardless of
which request thread asked for the render.
"""

import atexit
import json
import logging
import threading
from pathlib import Path
from queue import Queue

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Every asset under here is reachable from the rendered document; the
# paths themselves are resolved per-request by _serve_katex_asset from
# the URL the page asked for, including the font files katex.min.css
# pulls in via its own relative url(fonts/...) references.
KATEX_DIR = Path(__file__).resolve().parent / "vendor" / "katex"

# $$ must be checked before $ - auto-render tries delimiters in order at
# each position, and $ would otherwise match the opening/closing pair of a
# $$...$$ block as two empty $...$ matches instead of one display block.
_KATEX_DELIMITERS = [
    {"left": "$$", "right": "$$", "display": True},
    {"left": "$", "right": "$", "display": False},
]

DEFAULT_RENDER_TIMEOUT_SECONDS = 30.0
BROWSER_LAUNCH_TIMEOUT_SECONDS = 30.0

# Placeholder origin the document and its KaTeX assets are served from,
# via page.route() handlers that fulfill them from memory/local disk (see
# _process_job). Nothing ever resolves or connects to this host -
# Playwright intercepts the requests before the network layer - so the
# domain is deliberately one reserved for local/internal use rather than
# anything real that a misrouted request could actually reach.
#
# The KaTeX assets have to be served from this same origin rather than
# referenced as file:// URLs: Chromium refuses to load a file:// subresource
# from an http(s) document ("Not allowed to load local resource"), so the
# file:// references that worked when the document itself was a file://
# temp file would silently fail to load here, leaving renderMathInElement
# undefined and every math document timing out waiting for __katexDone.
_RENDER_ORIGIN = "http://assignment-pdf-renderer.localhost"
_RENDER_DOCUMENT_URL = f"{_RENDER_ORIGIN}/document.html"
_KATEX_URL_PREFIX = f"{_RENDER_ORIGIN}/katex/"


class PDFRenderError(RuntimeError):
    """A PDF could not be produced (browser/navigation/render failure)."""


def _validate_full_document(full_html: str) -> None:
    """
    `full_html` must be a complete, well-formed document containing both
    closing tags - callers always build one, so a missing tag is a caller
    bug worth failing loudly on rather than silently skipping typesetting
    (or, for math-free documents, silently skipping the __katexDone marker
    _process_job waits on).
    """
    if "</head>" not in full_html or "</body>" not in full_html:
        raise ValueError(
            "This HTML document requires </head> and </body> closing tags."
        )


def has_math(full_html: str) -> bool:
    """
    Cheap heuristic for "does this document contain any LaTeX to typeset".

    Every path that puts math into an assignment (both AI extraction
    prompts) is required to wrap it in "$...$"/"$$...$$", so this never
    false-negatives on real math. It can false-positive on incidental text
    like "candy costs $5" - harmless, since KaTeX's throwOnError: false
    already leaves non-math "$..." as plain text today, so a false
    positive just means the KaTeX assets got loaded for nothing, not that
    anything renders incorrectly.
    """
    return "$" in full_html


def inject_katex(full_html: str) -> str:
    """
    Insert the vendored KaTeX stylesheet before `</head>` and the KaTeX
    script/auto-render call before `</body>`.
    """
    _validate_full_document(full_html)

    head_addition = (
        f'<link rel="stylesheet" href="{_KATEX_URL_PREFIX}katex.min.css">\n</head>'
    )
    full_html = full_html.replace("</head>", head_addition, 1)

    body_addition = f"""
    <script src="{_KATEX_URL_PREFIX}katex.min.js"></script>
    <script src="{_KATEX_URL_PREFIX}contrib/auto-render.min.js"></script>
    <script>
      renderMathInElement(document.body, {{
        delimiters: {json.dumps(_KATEX_DELIMITERS)},
        throwOnError: false
      }});
      window.__katexDone = true;
    </script>
    </body>"""
    return full_html.replace("</body>", body_addition, 1)


def _mark_no_math_done(full_html: str) -> str:
    """
    Companion to inject_katex() for the has_math()-is-False path: skips
    loading any KaTeX asset entirely, but still sets window.__katexDone so
    _process_job's page.wait_for_function() doesn't wait out its full
    timeout for a marker that would otherwise never arrive.
    """
    _validate_full_document(full_html)
    return full_html.replace(
        "</body>", "<script>window.__katexDone = true;</script></body>", 1
    )


_KATEX_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".woff2": "font/woff2",
}


def _serve_katex_asset(route):
    """
    Fulfill a request for a vendored KaTeX asset from local disk.

    Serving these over the document's own origin (rather than as file://
    subresources, which Chromium blocks from an http document) means the
    stylesheet's own relative font references - url(fonts/KaTeX_*.woff2) -
    resolve back through here too, so no font handling is needed beyond
    the content-type mapping above.
    """
    requested = route.request.url.split("?", 1)[0].split("#", 1)[0]
    relative = requested[len(_KATEX_URL_PREFIX) :]

    # Resolve and confirm the result is still inside KATEX_DIR before
    # reading it. Every URL reaching this handler is one this module
    # authored or KaTeX's own stylesheet requested, so a traversal attempt
    # ("../../etc/passwd") is not a realistic threat here - but a path
    # built by string-joining untrusted-shaped input and read off disk
    # should be bounded on principle, not on the strength of an argument
    # about who can reach it.
    try:
        target = (KATEX_DIR / relative).resolve()
        target.relative_to(KATEX_DIR.resolve())
        body = target.read_bytes()
    except (ValueError, OSError):
        logger.warning("PDF renderer: refusing KaTeX asset request %r", requested)
        route.fulfill(status=404, body=b"")
        return

    route.fulfill(
        status=200,
        content_type=_KATEX_CONTENT_TYPES.get(
            target.suffix, "application/octet-stream"
        ),
        body=body,
    )


class _RenderJob:
    __slots__ = ("html", "options", "event", "result", "error")

    def __init__(self, html: str, options: dict):
        self.html = html
        self.options = options
        self.event = threading.Event()
        self.result: bytes | None = None
        self.error: PDFRenderError | None = None


class _ChromiumRenderWorker:
    """Owns one warm headless Chromium instance for this process's lifetime."""

    def __init__(self):
        self._jobs: "Queue[_RenderJob | None]" = Queue()
        self._ready = threading.Event()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run, name="chromium-pdf-renderer", daemon=True
        )
        self._thread.start()

        if not self._ready.wait(timeout=BROWSER_LAUNCH_TIMEOUT_SECONDS):
            raise PDFRenderError(
                "Timed out waiting for the PDF renderer's Chromium instance "
                "to start."
            )
        if self._startup_error is not None:
            raise self._startup_error

    def _run(self):
        try:
            playwright = sync_playwright().start()
            # --no-sandbox: Chromium's own sandbox needs kernel privileges
            # that are typically unavailable inside a container (Docker/
            # Railway); this is the standard, documented workaround for
            # running headless Chromium in a container.
            browser = playwright.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            self._startup_error = PDFRenderError(
                f"Failed to launch headless Chromium for PDF rendering: {exc}"
            )
            self._ready.set()
            return

        self._ready.set()

        try:
            while True:
                job = self._jobs.get()
                if job is None:  # shutdown sentinel
                    break
                self._process_job(browser, job)
        finally:
            try:
                browser.close()
            except Exception:
                logger.exception("Error closing Chromium during renderer shutdown")
            try:
                playwright.stop()
            except Exception:
                logger.exception("Error stopping Playwright during renderer shutdown")

    def _process_job(self, browser, job):
        page = None
        try:
            timeout_seconds = job.options.get("timeout", DEFAULT_RENDER_TIMEOUT_SECONDS)
            timeout_ms = timeout_seconds * 1000

            page = browser.new_page()

            # Serve the document straight from memory instead of writing it
            # to a temp file and navigating via file://. These routes match
            # only this placeholder origin, so Playwright never attempts
            # real DNS/socket resolution for them (routing happens before
            # the network layer) and any genuinely remote https:// question
            # image is left alone to load normally.
            def _serve_document(route):
                route.fulfill(status=200, content_type="text/html", body=job.html)

            page.route(_RENDER_DOCUMENT_URL, _serve_document)
            page.route(f"{_KATEX_URL_PREFIX}**", _serve_katex_asset)
            # "load" (not "networkidle"): a slow/unreachable remote
            # question_image shouldn't be able to stall the whole render
            # past a bounded timeout waiting for total network silence.
            page.goto(_RENDER_DOCUMENT_URL, wait_until="load", timeout=timeout_ms)
            page.wait_for_function("window.__katexDone === true", timeout=timeout_ms)

            # page.pdf() has no timeout parameter of its own in this
            # Playwright version - by the time it's called, the page has
            # already fully loaded and finished typesetting (both bounded
            # above), so the export step itself has no remaining unbounded
            # network/script wait to guard against directly. The outer
            # render()/gunicorn timeouts below are still the real backstop
            # if this step somehow hangs anyway.
            pdf_kwargs = {
                "format": job.options.get("page_format", "A4"),
                "print_background": True,
            }
            if job.options.get("margins"):
                pdf_kwargs["margin"] = job.options["margins"]
            header_template = job.options.get("header_template")
            footer_template = job.options.get("footer_template")
            if header_template or footer_template:
                pdf_kwargs["display_header_footer"] = True
                pdf_kwargs["header_template"] = header_template or "<span></span>"
                pdf_kwargs["footer_template"] = footer_template or "<span></span>"

            job.result = page.pdf(**pdf_kwargs)
        except PlaywrightTimeoutError as exc:
            job.error = PDFRenderError(f"PDF rendering timed out: {exc}")
        except Exception as exc:
            job.error = PDFRenderError(f"PDF rendering failed: {exc}")
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    logger.exception("Error closing Chromium page after PDF render")
            job.event.set()

    def render(self, html: str, **options) -> bytes:
        job = _RenderJob(html, options)
        self._jobs.put(job)

        timeout_seconds = options.get("timeout", DEFAULT_RENDER_TIMEOUT_SECONDS)
        # Slack beyond the in-page timeout so a job that is about to time
        # out on its own terms reports that specific error, rather than the
        # queue wait racing it and reporting a less useful generic timeout.
        #
        # Every Playwright call _process_job makes (goto, wait_for_function,
        # page.pdf) is given an explicit timeout, so this outer wait should
        # essentially never fire on its own. If it somehow does (e.g. a
        # Chromium-internal hang past its own timeout machinery), this
        # request thread gives up and reports failure, but the single
        # background worker thread stays stuck on that one job - no more
        # PDFs render in this process until it's restarted. There's no
        # cross-thread cancellation for a blocked Playwright sync call, and
        # forcibly closing the browser from another thread to unstick it
        # risks corrupting whatever job legitimately runs after. The
        # existing outer safety net is gunicorn's own --timeout (see
        # Dockerfile): if a request handler doesn't respond within that
        # window, gunicorn kills and replaces the whole worker process,
        # which takes any wedged browser/thread down with it.
        if not job.event.wait(timeout=timeout_seconds + 10):
            raise PDFRenderError("Timed out waiting for a PDF render job to complete.")
        if job.error is not None:
            raise job.error
        # _process_job's finally always sets job.event before returning, but
        # only its try block sets .result or .error - if some future change
        # to _process_job manages to skip both (e.g. an early return added
        # without setting either), this turns that into a loud, immediate
        # failure instead of silently handing an unsuspecting caller None
        # where this function's own signature promises bytes. An `assert`
        # would be the more usual way to spell this, but assert statements
        # are compiled out entirely under `python -O`, silently disabling
        # the check in exactly the deployment configuration where a
        # bug like this would be hardest to diagnose.
        if job.result is None:
            raise PDFRenderError("render job finished without a result or error")
        return job.result

    def shutdown(self):
        """Stop the background thread and close the browser."""
        self._jobs.put(None)
        self._thread.join(timeout=10)


_worker = None
_worker_lock = threading.Lock()


def _get_worker() -> _ChromiumRenderWorker:
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                _worker = _ChromiumRenderWorker()
    return _worker


def _shutdown_worker():
    """
    Tear down and clear the singleton, closing its Chromium instance.

    Registered with atexit below so a normal process exit (e.g. gunicorn
    recycling a worker after --max-requests, which sends SIGTERM and lets
    the worker finish its Python shutdown sequence) always closes the
    browser explicitly. Without this, the owning thread is a daemon
    thread - it would simply be killed mid-flight on interpreter exit,
    with no guarantee Chromium's own subprocess gets closed alongside it,
    risking an orphaned browser process surviving each worker recycle.
    """
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.shutdown()
            _worker = None


# Test-facing alias: same operation, named for what tests use it for
# (starting each test/class from a clean singleton) rather than what it
# protects against in production.
reset_worker_for_tests = _shutdown_worker

atexit.register(_shutdown_worker)


def render_html_to_pdf(
    full_html: str,
    *,
    header_template: str = "",
    footer_template: str = "",
    margins: dict | None = None,
    page_format: str = "A4",
    timeout: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> bytes:
    """
    Render a complete HTML document to PDF bytes via the shared, warm
    Chromium instance, with math typesetting via KaTeX injected first -
    unless has_math() finds nothing to typeset, in which case the KaTeX
    assets are skipped entirely rather than loaded for no reason.

    `full_html` must contain `</head>` and `</body>` (see inject_katex).
    `margins` is a dict of Playwright's page.pdf() margin keys ("top",
    "right", "bottom", "left"), each a CSS length string (e.g. "2cm").
    """
    prepared_html = (
        inject_katex(full_html)
        if has_math(full_html)
        else _mark_no_math_done(full_html)
    )
    worker = _get_worker()
    return worker.render(
        prepared_html,
        header_template=header_template,
        footer_template=footer_template,
        margins=margins,
        page_format=page_format,
        timeout=timeout,
    )
