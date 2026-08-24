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

Concurrency model: one dedicated background thread per process runs an
asyncio event loop that owns the Playwright connection, the warm browser,
and every page. Request threads never touch Playwright directly - they
submit a coroutine with asyncio.run_coroutine_threadsafe() and block on
the returned future - so the public API stays synchronous for Django's
sync views while the renders themselves run *concurrently* as coroutines
on that loop.

That concurrency is the point: an earlier revision drove the same warm
browser from a single worker thread pulling one job at a time off a
queue, which meant a burst of downloads for distinct (uncached)
assignments queued behind each other and the tail latency was the sum of
the queue, not the cost of one render. Chromium is perfectly happy
running several pages at once in one browser, so the loop opens several
concurrently, bounded by a semaphore (PDF_RENDERER_MAX_CONCURRENT_RENDERS)
so peak memory stays predictable rather than scaling with request volume.

Browser lifecycle (recycling after N renders, discarding a browser that
crashed) is coordinated through a swap lock plus an in-flight counter.
Nothing ever blocks while holding that lock: a healthy browser is
recycled only at a moment when no render is in flight, because waiting
for in-flight work to drain would queue every other render behind the
slowest one - see _acquire_browser.
"""

import asyncio
import atexit
import json
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from django.conf import settings
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

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
# _do_render). Nothing ever resolves or connects to this host -
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


def _max_renders_per_browser() -> int:
    """
    How many renders one Chromium instance handles before being recycled;
    0 disables recycling entirely.

    The default is set from a real 120-render soak: memory grew ~0.02
    MB/render and was decelerating, so 500 renders bounds growth to a
    handful of MB while rarely firing under gunicorn (whose own
    --max-requests recycles the whole worker every 800-1200 requests, only
    some of which are PDF downloads). See _acquire_browser for why this
    exists at all given that measurement.
    """
    return getattr(settings, "PDF_RENDERER_MAX_RENDERS_PER_BROWSER", 500)


def _max_concurrent_renders() -> int:
    """
    How many pages this process renders at once in its one warm browser.

    Measured cost of an open page is ~20MB on top of the browser's ~165MB
    baseline, so this is the knob that decides peak memory per worker:
    the default of 4 bounds a worker at roughly baseline + 80MB even under
    a heavy burst, while still removing the serialization that made tail
    latency the sum of the queue. Must be at least 1.

    Unlike _max_renders_per_browser(), which is consulted on every
    acquire, this is read once when the worker starts - an asyncio
    semaphore cannot be resized after creation. Changing it therefore
    takes effect on the next process (or, in tests, after
    reset_worker_for_tests()).
    """
    return max(1, getattr(settings, "PDF_RENDERER_MAX_CONCURRENT_RENDERS", 4))


class PDFRenderError(RuntimeError):
    """A PDF could not be produced (browser/navigation/render failure)."""


def _validate_full_document(full_html: str) -> None:
    """
    `full_html` must be a complete, well-formed document containing both
    closing tags - callers always build one, so a missing tag is a caller
    bug worth failing loudly on rather than silently skipping typesetting
    (or, for math-free documents, silently skipping the __katexDone marker
    _do_render waits on).
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
    _do_render's page.wait_for_function() doesn't wait out its full
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

# The vendored assets never change while the process runs, and with
# several pages rendering at once the same handful of files would
# otherwise be re-read from disk on every render. Only ever touched from
# the renderer's event-loop thread, so it needs no lock.
_katex_asset_cache: dict = {}


def _read_katex_asset(relative: str):
    """
    Resolve one vendored KaTeX asset, or None if the path escapes
    KATEX_DIR or doesn't exist.

    Every URL reaching this is one this module authored or KaTeX's own
    stylesheet requested, so a traversal attempt ("../../etc/passwd") is
    not a realistic threat here - but a path built by string-joining
    untrusted-shaped input and read off disk should be bounded on
    principle, not on the strength of an argument about who can reach it.
    """
    if relative in _katex_asset_cache:
        return _katex_asset_cache[relative]

    try:
        target = (KATEX_DIR / relative).resolve()
        target.relative_to(KATEX_DIR.resolve())
        body = target.read_bytes()
    except (ValueError, OSError):
        return None

    content_type = _KATEX_CONTENT_TYPES.get(target.suffix, "application/octet-stream")
    _katex_asset_cache[relative] = (content_type, body)
    return _katex_asset_cache[relative]


async def _serve_katex_asset(route):
    """
    Fulfill a request for a vendored KaTeX asset.

    Serving these over the document's own origin (rather than as file://
    subresources, which Chromium blocks from an http document) means the
    stylesheet's own relative font references - url(fonts/KaTeX_*.woff2) -
    resolve back through here too, so no font handling is needed beyond
    the content-type mapping above.
    """
    requested = route.request.url.split("?", 1)[0].split("#", 1)[0]
    asset = _read_katex_asset(requested[len(_KATEX_URL_PREFIX) :])

    if asset is None:
        logger.warning("PDF renderer: refusing KaTeX asset request %r", requested)
        await route.fulfill(status=404, body=b"")
        return

    content_type, body = asset
    await route.fulfill(status=200, content_type=content_type, body=body)


class _ChromiumRenderWorker:
    """
    Owns this process's Playwright connection and its one warm Chromium
    instance, on a dedicated asyncio event loop thread.
    """

    def __init__(self):
        self._loop = None
        self._playwright = None
        self._browser = None
        self._renders_since_launch = 0
        self._in_flight = 0

        # Safe to build here rather than on the loop: since Python 3.10
        # asyncio primitives no longer capture a loop at construction,
        # they bind to the running loop on first await - and these are
        # only ever awaited from the renderer's own loop thread. Building
        # them here also means they are never None once __init__ returns,
        # so nothing downstream has to defend against that.
        self._swap_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(_max_concurrent_renders())

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

    # --- browser lifecycle -------------------------------------------------

    async def _launch(self, playwright):
        # --no-sandbox: Chromium's own sandbox needs kernel privileges
        # that are typically unavailable inside a container (Docker/
        # Railway); this is the standard, documented workaround for
        # running headless Chromium in a container.
        return await playwright.chromium.launch(args=["--no-sandbox"])

    @staticmethod
    def _is_alive(browser) -> bool:
        """
        Whether this browser is still usable for the next render.

        Treats a raising is_connected() as dead too: the question being
        asked is "can this still render", and a browser object that can't
        answer cannot render either.
        """
        try:
            return bool(browser.is_connected())
        except Exception:
            return False

    async def _close_browser(self):
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.exception("[PDF] error closing Chromium")
            self._browser = None

    def _needs_recycle(self) -> bool:
        bound = _max_renders_per_browser()
        return bool(bound) and self._renders_since_launch >= bound

    async def _acquire_browser(self):
        """
        Hand out the browser for one render, replacing it first if it is
        missing, dead, or has hit its render bound.

        The swap lock makes the decision to replace atomic. Crucially,
        nothing here ever *waits* while holding it: a healthy browser is
        only recycled when no render is in flight, so one slow render can
        never stall every other render queued behind this lock.

        Recycling itself is not fixing an observed leak: a 120-render soak
        measured ~0.02 MB/render, decelerating, and gunicorn already
        recycles whole workers every --max-requests. It's a bound against
        a future Chromium that does leak, and against callers with no
        process-level recycling of their own (a Celery task has no
        --max-requests equivalent).

        Raises if a relaunch fails, which fails just this render - the
        browser is left as None so the next one retries rather than the
        process staying wedged.
        """
        async with self._swap_lock:
            if self._browser is not None and not self._is_alive(self._browser):
                # Chromium died outright - OOM killer, crash, container
                # kill. Without this the dead object would stay in place
                # and fail every later render identically. Replaced
                # immediately even with renders in flight: those are using
                # a browser that no longer exists, so they are already
                # lost (and _render retries them on the new one).
                logger.warning(
                    "[PDF] Chromium is no longer connected (crashed or was "
                    "killed) - discarding it and relaunching."
                )
                await self._close_browser()
                self._renders_since_launch = 0

            elif self._needs_recycle() and self._in_flight == 0:
                # Recycle only while nothing is mid-render.
                #
                # The obvious alternative - wait for in-flight renders to
                # drain, then swap - stalls the whole process: this runs
                # under the swap lock, so every other render queues behind
                # the slowest one still running. Measured with a
                # deliberately hung render, that dragged nine ~0.2s
                # renders out to ~5s each.
                #
                # Deferring instead costs nothing that matters. The bound
                # is memory hygiene, not correctness (a 120-render soak
                # measured ~0.02 MB/render, decelerating), so "recycle at
                # the next quiet moment past N" is as good as "recycle
                # exactly at N" - and under load heavy enough that the
                # renderer is never idle, gunicorn's --max-requests
                # recycles the whole worker anyway.
                logger.info(
                    "[PDF] recycling Chromium after %s renders (bound: %s)",
                    self._renders_since_launch,
                    _max_renders_per_browser(),
                )
                await self._close_browser()
                self._renders_since_launch = 0

            if self._browser is None:
                try:
                    self._browser = await self._launch(self._playwright)
                except Exception as exc:
                    # _browser stays None, so the next render retries the
                    # launch instead of this process's renderer staying
                    # wedged.
                    raise PDFRenderError(
                        f"Failed to relaunch headless Chromium for PDF "
                        f"rendering: {exc}"
                    ) from exc
                self._renders_since_launch = 0

            self._renders_since_launch += 1
            self._in_flight += 1
            return self._browser

    async def _release_browser(self):
        self._in_flight -= 1
        if self._in_flight < 0:  # pragma: no cover - defensive
            self._in_flight = 0

    # --- event loop --------------------------------------------------------

    async def _startup(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._launch(self._playwright)

    async def _shutdown(self):
        await self._close_browser()
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.exception("Error stopping Playwright during renderer shutdown")
            self._playwright = None

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._startup())
        except Exception as exc:
            self._startup_error = PDFRenderError(
                f"Failed to launch headless Chromium for PDF rendering: {exc}"
            )
            self._ready.set()
            try:
                loop.run_until_complete(self._shutdown())
            except Exception:
                logger.exception("Error cleaning up after a failed renderer startup")
            loop.close()
            return

        self._ready.set()

        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(self._shutdown())
            except Exception:
                logger.exception("Error during renderer shutdown")
            try:
                loop.close()
            except Exception:
                logger.exception("Error closing the renderer event loop")

    # --- rendering ---------------------------------------------------------

    async def _do_render(self, browser, html: str, options: dict) -> bytes:
        timeout_seconds = options.get("timeout", DEFAULT_RENDER_TIMEOUT_SECONDS)
        timeout_ms = timeout_seconds * 1000
        page = None
        try:
            page = await browser.new_page()

            # Serve the document straight from memory instead of writing it
            # to a temp file and navigating via file://. These routes match
            # only this placeholder origin, so Playwright never attempts
            # real DNS/socket resolution for them (routing happens before
            # the network layer) and any genuinely remote https:// question
            # image is left alone to load normally. Routes are registered
            # per-page, so concurrent renders can never serve each other's
            # document.
            async def _serve_document(route):
                await route.fulfill(status=200, content_type="text/html", body=html)

            await page.route(_RENDER_DOCUMENT_URL, _serve_document)
            await page.route(f"{_KATEX_URL_PREFIX}**", _serve_katex_asset)
            # "load" (not "networkidle"): a slow/unreachable remote
            # question_image shouldn't be able to stall the whole render
            # past a bounded timeout waiting for total network silence.
            await page.goto(_RENDER_DOCUMENT_URL, wait_until="load", timeout=timeout_ms)
            await page.wait_for_function(
                "window.__katexDone === true", timeout=timeout_ms
            )

            # page.pdf() has no timeout parameter of its own in this
            # Playwright version - by the time it's called, the page has
            # already fully loaded and finished typesetting (both bounded
            # above), so the export step itself has no remaining unbounded
            # network/script wait to guard against directly. The outer
            # render()/gunicorn timeouts below are still the real backstop
            # if this step somehow hangs anyway.
            pdf_kwargs = {
                "format": options.get("page_format", "A4"),
                "print_background": True,
            }
            if options.get("margins"):
                pdf_kwargs["margin"] = options["margins"]
            header_template = options.get("header_template")
            footer_template = options.get("footer_template")
            if header_template or footer_template:
                pdf_kwargs["display_header_footer"] = True
                pdf_kwargs["header_template"] = header_template or "<span></span>"
                pdf_kwargs["footer_template"] = footer_template or "<span></span>"

            return await page.pdf(**pdf_kwargs)
        except PlaywrightTimeoutError as exc:
            raise PDFRenderError(f"PDF rendering timed out: {exc}") from exc
        except Exception as exc:
            raise PDFRenderError(f"PDF rendering failed: {exc}") from exc
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    logger.exception("Error closing Chromium page after PDF render")

    async def _render(self, html: str, options: dict) -> bytes:
        # The semaphore bounds how many pages exist at once; acquiring the
        # browser only after a slot is free means a burst waits here
        # rather than opening an unbounded number of pages.
        async with self._semaphore:
            for attempt in (1, 2):
                browser = await self._acquire_browser()
                try:
                    return await self._do_render(browser, html, options)
                except PDFRenderError:
                    # If Chromium died mid-render (OOM killer, crash), the
                    # render failed for a reason that has nothing to do
                    # with this document - every render in flight at that
                    # moment dies together. Retrying once on a fresh
                    # browser turns that into a slower download instead of
                    # a failed one. Deliberately narrow: the retry only
                    # happens when the browser is actually gone, so a
                    # timeout or a genuinely broken document still fails
                    # immediately rather than being rendered twice.
                    if attempt == 1 and not self._is_alive(browser):
                        logger.warning(
                            "[PDF] render failed because Chromium went away - "
                            "retrying once on a fresh browser."
                        )
                        continue
                    raise
                finally:
                    await self._release_browser()

        # Unreachable: the second attempt always either returns or
        # re-raises, since the retry guard only fires on attempt 1. Here
        # so this can never silently fall through and hand a caller None
        # where the signature promises bytes.
        raise PDFRenderError("PDF rendering failed after a retry.")

    def render(self, html: str, **options) -> bytes:
        if self._loop is None:  # pragma: no cover - defensive
            raise PDFRenderError("The PDF renderer's event loop is not running.")

        timeout_seconds = options.get("timeout", DEFAULT_RENDER_TIMEOUT_SECONDS)
        future = asyncio.run_coroutine_threadsafe(
            self._render(html, options), self._loop
        )

        # Slack beyond the in-page timeout because a render may also wait
        # for a concurrency slot before it starts. Every Playwright call
        # inside _do_render carries its own timeout, so this outer bound
        # should essentially never be what fires; gunicorn's --timeout
        # (see Dockerfile) remains the real backstop for a wedged worker.
        try:
            return future.result(timeout=timeout_seconds + 15)
        except FutureTimeoutError:
            future.cancel()
            raise PDFRenderError(
                "Timed out waiting for a PDF render job to complete."
            ) from None

    def shutdown(self):
        """Stop the event loop and close the browser."""
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                # Loop already stopped/closed.
                pass
        self._thread.join(timeout=15)


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
    browser explicitly. Without this, the loop thread is a daemon thread -
    it would simply be killed mid-flight on interpreter exit, with no
    guarantee Chromium's own subprocess gets closed alongside it, risking
    an orphaned browser process surviving each worker recycle.
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

    Blocks the calling thread until the PDF is ready, but several callers
    can be in here at once: the renders run concurrently on the renderer's
    event loop, bounded by PDF_RENDERER_MAX_CONCURRENT_RENDERS.

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
