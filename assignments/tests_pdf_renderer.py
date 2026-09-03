"""Tests for assignments.pdf_renderer - the warm-Chromium PDF pipeline that
replaced WeasyPrint so assignment downloads can typeset LaTeX via KaTeX.

Split into two layers:
  * KaTeXInjectionTest      - pure string-manipulation logic, no browser
  * ChromiumRendererTest    - the real thing, using the actual vendored
    KaTeX assets and a real headless Chromium instance. Skipped (not
    failed) if Chromium isn't available in the environment running the
    suite, mirroring the defensive pattern already used by
    ai_processor/benchmark/render.py for the same reason.
"""

import threading
import time
import unittest
from unittest.mock import patch

import fitz
from django.test import SimpleTestCase, override_settings

from assignments import pdf_renderer


def _chromium_available():
    """
    Probe once whether a real headless Chromium launch succeeds, so the
    real-rendering tests can skip cleanly in an environment without a
    matching browser installed, rather than failing the whole suite.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            browser.close()
        return True
    except Exception:
        return False


_CHROMIUM_AVAILABLE = _chromium_available()


class KaTeXInjectionTest(SimpleTestCase):
    def _full_html(self, body="<p>hello</p>"):
        return f"<!doctype html><html><head><title>T</title></head><body>{body}</body></html>"

    def test_raises_without_closing_head_tag(self):
        with self.assertRaises(ValueError):
            pdf_renderer.inject_katex("<html><body>no head close</body></html>")

    def test_raises_without_closing_body_tag(self):
        with self.assertRaises(ValueError):
            pdf_renderer.inject_katex("<html><head></head>no body close</html>")

    def test_inserts_katex_stylesheet_before_head_close(self):
        result = pdf_renderer.inject_katex(self._full_html())
        self.assertIn(
            f'<link rel="stylesheet" '
            f'href="{pdf_renderer._KATEX_URL_PREFIX}katex.min.css">',
            result,
        )
        # The stylesheet must land inside <head>, not appended anywhere else.
        head_content = result.split("</head>")[0]
        self.assertIn("katex.min.css", head_content)

    def test_inserts_katex_and_autorender_scripts_before_body_close(self):
        result = pdf_renderer.inject_katex(self._full_html())
        body_content = result.split("<body>")[1]
        self.assertIn(
            f'src="{pdf_renderer._KATEX_URL_PREFIX}katex.min.js"', body_content
        )
        self.assertIn(
            f'src="{pdf_renderer._KATEX_URL_PREFIX}contrib/auto-render.min.js"',
            body_content,
        )
        self.assertIn("renderMathInElement", body_content)
        self.assertIn("window.__katexDone = true", body_content)

    def test_katex_assets_are_same_origin_not_file_urls(self):
        """
        Chromium refuses to load a file:// subresource from an http(s)
        document ("Not allowed to load local resource"), and the document
        is now served over a placeholder http origin via page.route()
        rather than from a file:// temp file. So the KaTeX references must
        be same-origin URLs served through that same interception - a
        regression back to file:// here would leave renderMathInElement
        undefined and every math document timing out waiting for
        __katexDone.
        """
        result = pdf_renderer.inject_katex(self._full_html())
        self.assertNotIn("file://", result)
        self.assertIn(pdf_renderer._KATEX_URL_PREFIX, result)

    def test_double_dollar_delimiter_is_checked_before_single_dollar(self):
        """
        auto-render tries delimiters in array order at each scan position -
        if "$" were listed before "$$", a "$$...$$" block would be
        misparsed as two empty "$...$" matches instead of one display
        block. Assert the safe order is what actually ships.
        """
        result = pdf_renderer.inject_katex(self._full_html())
        script_section = result.split("renderMathInElement")[1]
        double_dollar_pos = script_section.find('"$$"')
        single_dollar_pos = script_section.find('"$"')
        self.assertNotEqual(double_dollar_pos, -1)
        self.assertNotEqual(single_dollar_pos, -1)
        self.assertLess(double_dollar_pos, single_dollar_pos)

    def test_preserves_original_body_content(self):
        result = pdf_renderer.inject_katex(self._full_html("<p>Keep me</p>"))
        self.assertIn("<p>Keep me</p>", result)

    def test_only_replaces_first_occurrence_of_each_closing_tag(self):
        """
        A document that happens to contain the literal string "</head>" or
        "</body>" inside a script/text node (unlikely, but not impossible
        for AI-authored content) must only get KaTeX inserted once, at the
        real closing tag - not duplicated at every occurrence.
        """
        html = (
            "<!doctype html><html><head><title>T</title>"
            "<script>var x = '</head>';</script></head>"
            "<body><p>hi</p></body></html>"
        )
        result = pdf_renderer.inject_katex(html)
        self.assertEqual(result.count("katex.min.css"), 1)
        self.assertEqual(result.count("window.__katexDone"), 1)


class HasMathTest(SimpleTestCase):
    def test_detects_inline_and_display_math(self):
        self.assertTrue(pdf_renderer.has_math("<p>Solve $x = 5$.</p>"))
        self.assertTrue(pdf_renderer.has_math("<p>$$x = 5$$</p>"))

    def test_no_dollar_sign_means_no_math(self):
        self.assertFalse(pdf_renderer.has_math("<p>Name the capital of France.</p>"))
        self.assertFalse(pdf_renderer.has_math(""))

    def test_incidental_dollar_sign_is_treated_as_math(self):
        """
        Deliberate false-positive: a word problem mentioning "$5" loads
        KaTeX for nothing. Documented as acceptable because KaTeX's
        throwOnError:false leaves unparseable "$..." as plain text, so the
        only cost is a little wasted work - never wrong output. Asserted
        so the tradeoff is visible rather than a surprise.
        """
        self.assertTrue(pdf_renderer.has_math("<p>The candy costs $5 total.</p>"))


class MarkNoMathDoneTest(SimpleTestCase):
    def _full_html(self, body="<p>hello</p>"):
        return f"<!doctype html><html><head><title>T</title></head><body>{body}</body></html>"

    def test_sets_katex_done_without_loading_any_katex_asset(self):
        """
        The whole point of the no-math path: skip the KaTeX CSS/JS
        entirely, but still set __katexDone, which _process_job's
        wait_for_function() blocks on - omitting it would make every
        math-free render wait out the full timeout and then fail.
        """
        result = pdf_renderer._mark_no_math_done(self._full_html())
        self.assertIn("window.__katexDone = true", result)
        self.assertNotIn("katex.min.css", result)
        self.assertNotIn("katex.min.js", result)
        self.assertNotIn("renderMathInElement", result)

    def test_validates_the_document_like_inject_katex_does(self):
        with self.assertRaises(ValueError):
            pdf_renderer._mark_no_math_done("<html><head></head>no body close</html>")

    def test_preserves_original_body_content(self):
        result = pdf_renderer._mark_no_math_done(self._full_html("<p>Keep me</p>"))
        self.assertIn("<p>Keep me</p>", result)


@unittest.skipUnless(
    _CHROMIUM_AVAILABLE,
    "Headless Chromium is not available in this environment - install it "
    "with `playwright install chromium` to run these tests.",
)
class ChromiumRendererTest(SimpleTestCase):
    """
    Exercises the real renderer against a real, warm Chromium instance -
    the whole point of this module. No mocking of Playwright here; these
    are the tests that would have caught "the vendored KaTeX path is
    wrong" or "the header/footer template syntax doesn't work" bugs that a
    fully-mocked test suite structurally cannot catch.
    """

    @classmethod
    def tearDownClass(cls):
        pdf_renderer.reset_worker_for_tests()
        super().tearDownClass()

    def _full_html(self, body):
        return f"""<!doctype html><html><head><meta charset="utf-8">
        <title>Test Document</title></head><body>{body}</body></html>"""

    def _extract_text(self, pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)

    def test_returns_valid_pdf_bytes(self):
        pdf_bytes = pdf_renderer.render_html_to_pdf(
            self._full_html("<p>Hello, world.</p>")
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_math_free_document_renders_without_loading_katex(self):
        """
        End-to-end proof of the no-math fast path: a document with no "$"
        must still render correctly (not hang waiting for __katexDone,
        which the KaTeX bundle would normally set) while skipping the
        KaTeX assets entirely.
        """
        pdf_bytes = pdf_renderer.render_html_to_pdf(
            self._full_html("<p>Name the capital of France.</p>")
        )
        text = self._extract_text(pdf_bytes)
        self.assertIn("Name the capital of France.", text)

    def test_katex_fonts_load_through_the_intercepted_origin(self):
        """
        katex.min.css requests its fonts via relative url(fonts/*.woff2),
        which resolve back through the same route handler that served the
        stylesheet. If that font route regressed, KaTeX would silently
        fall back to a system font and the typeset math would still
        "work" - so assert real KaTeX font resources actually made it
        into the PDF rather than only checking the text came out.
        """
        pdf_bytes = pdf_renderer.render_html_to_pdf(
            self._full_html(r"<p>$\frac{1}{2} + \sqrt{16}$</p>")
        )
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        embedded_fonts = {font[3] for page in doc for font in page.get_fonts(full=True)}
        self.assertTrue(
            any("KaTeX" in name for name in embedded_fonts),
            f"no KaTeX font embedded in the PDF; found: {embedded_fonts}",
        )

    def test_math_is_typeset_not_left_as_literal_latex(self):
        html = self._full_html(r"<p>Solve for $x$: $2x + 5 = 15$.</p>")
        pdf_bytes = pdf_renderer.render_html_to_pdf(html)
        text = self._extract_text(pdf_bytes)

        # The literal LaTeX source must be gone - KaTeX replaced it with
        # real typeset glyphs, not left it as inert text.
        self.assertNotIn("$x$", text)
        self.assertNotIn("$2x", text)
        self.assertNotIn("\\begin", text)
        # And the typeset numbers/letters are still present as real,
        # extractable text (proves this isn't a rasterized image).
        self.assertIn("Solve for", text)
        self.assertIn("2", text)
        self.assertIn("5", text)
        self.assertIn("15", text)

    def test_matrix_environment_renders_as_extractable_text(self):
        """
        Regression target: this is the exact case (a bmatrix) that started
        this whole investigation - a matrix rendered as literal
        "$\\begin{bmatrix}...\\end{bmatrix}$" text instead of a typeset
        matrix. Matplotlib's mathtext (the pure-Python alternative that
        was considered and rejected) cannot render this construct at all;
        this asserts the chosen approach (KaTeX via real Chromium) can.
        """
        html = self._full_html(
            r"<p>$A = \begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}$</p>"
        )
        pdf_bytes = pdf_renderer.render_html_to_pdf(html)
        text = self._extract_text(pdf_bytes)

        self.assertNotIn("begin{bmatrix}", text)
        self.assertNotIn("\\\\", text)
        for digit in ("1", "2", "3", "4"):
            self.assertIn(digit, text)

    def test_header_template_title_is_filled_in(self):
        pdf_bytes = pdf_renderer.render_html_to_pdf(
            self._full_html("<p>Body content.</p>"),
            header_template=(
                '<div style="font-size:9px; width:100%; text-align:center;">'
                '<span class="title"></span></div>'
            ),
            margins={"top": "1.5cm", "bottom": "1.5cm", "left": "1cm", "right": "1cm"},
        )
        text = self._extract_text(pdf_bytes)
        self.assertIn("Test Document", text)  # from <title>Test Document</title>

    def test_footer_template_page_numbers_are_filled_in(self):
        pdf_bytes = pdf_renderer.render_html_to_pdf(
            self._full_html("<p>Body content.</p>"),
            footer_template=(
                '<div style="font-size:8.5px; width:100%; text-align:center;">'
                'Page <span class="pageNumber"></span> of '
                '<span class="totalPages"></span></div>'
            ),
            margins={"top": "1.5cm", "bottom": "1.5cm", "left": "1cm", "right": "1cm"},
        )
        text = self._extract_text(pdf_bytes)
        self.assertRegex(text, r"Page\s*1\s*of\s*1")

    def test_missing_closing_tags_raise_before_touching_the_browser(self):
        with self.assertRaises(ValueError):
            pdf_renderer.render_html_to_pdf("<html><body>no head</body></html>")

    def test_render_is_thread_safe_under_concurrent_use(self):
        """
        Django serves requests with gthread workers (multiple threads per
        process) - several threads must be able to call
        render_html_to_pdf() at the same time without corrupting each
        other's output or crashing the single shared Chromium instance.
        """
        results = {}
        errors = []

        def render(n):
            try:
                html = self._full_html(f"<p>Concurrent job number {n}.</p>")
                pdf_bytes = pdf_renderer.render_html_to_pdf(html)
                results[n] = self._extract_text(pdf_bytes)
            except Exception as exc:  # pragma: no cover - failure path only
                errors.append((n, exc))

        threads = [threading.Thread(target=render, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        for n, text in results.items():
            self.assertIn(f"Concurrent job number {n}.", text)
            # Every other job's number must NOT leak into this one's PDF.
            for other in range(6):
                if other != n:
                    self.assertNotIn(f"job number {other}.", text)


@unittest.skipUnless(
    _CHROMIUM_AVAILABLE,
    "Headless Chromium is not available in this environment.",
)
class BrowserRecyclingTest(SimpleTestCase):
    """
    Recycling the warm browser after N renders. Uses a tiny N via
    override_settings so the behaviour is exercised in a couple of
    renders rather than the production default of 500.
    """

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()

    def tearDown(self):
        pdf_renderer.reset_worker_for_tests()

    def _full_html(self, body):
        return f"""<!doctype html><html><head><meta charset="utf-8">
        <title>Recycle Test</title></head><body>{body}</body></html>"""

    def _text(self, pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)

    @override_settings(PDF_RENDERER_MAX_RENDERS_PER_BROWSER=2)
    def test_renders_keep_working_across_a_recycle(self):
        """
        The point of the feature is that recycling is invisible to
        callers: with a bound of 2, six renders span several browser
        instances and every one must still come back correct.
        """
        for i in range(6):
            pdf_bytes = pdf_renderer.render_html_to_pdf(
                self._full_html(f"<p>Render number {i}.</p>")
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
            self.assertIn(f"Render number {i}.", self._text(pdf_bytes))

    @override_settings(PDF_RENDERER_MAX_RENDERS_PER_BROWSER=2)
    def test_recycle_actually_replaces_the_browser_instance(self):
        """
        Guards against the bound silently never firing (e.g. a counter
        that resets every job): with a bound of 2, three renders must
        trigger at least one relaunch.
        """
        with patch.object(
            pdf_renderer._ChromiumRenderWorker,
            "_launch",
            autospec=True,
            side_effect=pdf_renderer._ChromiumRenderWorker._launch,
        ) as mock_launch:
            for i in range(3):
                pdf_renderer.render_html_to_pdf(self._full_html(f"<p>{i}</p>"))

        # 1 initial launch + at least 1 relaunch after hitting the bound.
        self.assertGreaterEqual(mock_launch.call_count, 2)

    @override_settings(PDF_RENDERER_MAX_RENDERS_PER_BROWSER=0)
    def test_zero_disables_recycling(self):
        with patch.object(
            pdf_renderer._ChromiumRenderWorker,
            "_launch",
            autospec=True,
            side_effect=pdf_renderer._ChromiumRenderWorker._launch,
        ) as mock_launch:
            for i in range(4):
                pdf_renderer.render_html_to_pdf(self._full_html(f"<p>{i}</p>"))

        self.assertEqual(mock_launch.call_count, 1)

    def test_a_dead_browser_is_discarded_and_the_next_render_recovers(self):
        """
        Chromium can die outright - OOM killer, crash, container kill.
        Without detection the dead browser object stays in place and every
        later render fails identically, wedging this process until the
        whole worker recycles.

        The death is simulated by reporting the browser as disconnected
        once, rather than by actually killing chrome-headless-shell:
        a real `pkill` would take down every headless browser on the
        machine, including other tests running concurrently. The recovery
        path under test is the same either way - it keys off _is_alive -
        and a genuine kill is exercised separately, outside the suite.
        """
        real_is_alive = pdf_renderer._ChromiumRenderWorker._is_alive
        calls = {"n": 0}

        def dies_once(browser):
            calls["n"] += 1
            if calls["n"] == 1:
                return False  # "the browser just crashed"
            return real_is_alive(browser)

        with patch.object(
            pdf_renderer._ChromiumRenderWorker,
            "_is_alive",
            staticmethod(dies_once),
        ):
            first = pdf_renderer.render_html_to_pdf(self._full_html("<p>before</p>"))
            self.assertTrue(first.startswith(b"%PDF-"))
            # That render reported the browser dead afterwards, so it was
            # discarded; this one must transparently relaunch and work.
            after = pdf_renderer.render_html_to_pdf(self._full_html("<p>after</p>"))

        self.assertTrue(after.startswith(b"%PDF-"))
        self.assertIn("after", self._text(after))
        self.assertGreaterEqual(calls["n"], 1)

    @override_settings(PDF_RENDERER_MAX_RENDERS_PER_BROWSER=1)
    def test_a_failed_relaunch_costs_one_render_then_recovers(self):
        """
        A transient relaunch failure must cost exactly one render, not
        wedge this process's renderer forever.

        Recycling happens lazily when a render *acquires* the browser
        (rather than eagerly after the previous one finished), so it is
        the render that trips the bound which pays for a failed relaunch -
        and the one after it retries the launch and succeeds. Doing it at
        acquire time means a bound reached at the end of a burst never
        pays for a recycle no later render would have used.
        """
        real_launch = pdf_renderer._ChromiumRenderWorker._launch
        calls = {"n": 0}

        async def flaky_launch(self, playwright):
            calls["n"] += 1
            # Fail only the first relaunch (2nd launch overall); the
            # initial launch and later retries succeed.
            if calls["n"] == 2:
                raise RuntimeError("simulated relaunch failure")
            return await real_launch(self, playwright)

        with patch.object(pdf_renderer._ChromiumRenderWorker, "_launch", flaky_launch):
            # Uses the browser launched at startup.
            first = pdf_renderer.render_html_to_pdf(self._full_html("<p>one</p>"))
            self.assertTrue(first.startswith(b"%PDF-"))

            # Trips the bound of 1; its relaunch fails, so this render
            # fails - with a clear error, not a raw Playwright one.
            with self.assertRaises(pdf_renderer.PDFRenderError):
                pdf_renderer.render_html_to_pdf(self._full_html("<p>two</p>"))

            # ...and the renderer is not wedged: the next one relaunches.
            third = pdf_renderer.render_html_to_pdf(self._full_html("<p>three</p>"))
            self.assertTrue(third.startswith(b"%PDF-"))
            self.assertIn("three", self._text(third))


@unittest.skipUnless(
    _CHROMIUM_AVAILABLE,
    "Headless Chromium is not available in this environment.",
)
class ConcurrentRenderingTest(SimpleTestCase):
    """
    Renders run concurrently on the renderer's event loop rather than
    queueing behind one another, bounded by a semaphore.

    The concurrency setting is read once when the worker starts, so each
    test resets the singleton to pick up its own override.
    """

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()

    def tearDown(self):
        pdf_renderer.reset_worker_for_tests()

    def _full_html(self, body):
        return f"""<!doctype html><html><head><meta charset="utf-8">
        <title>Concurrency Test</title></head><body>{body}</body></html>"""

    def _text(self, pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)

    def _render_many(self, count):
        """Render `count` uniquely-marked documents from `count` threads."""
        outputs = {}
        errors = []
        lock = threading.Lock()

        def work(i):
            try:
                pdf_bytes = pdf_renderer.render_html_to_pdf(
                    self._full_html(rf"<p>JOB{i}: $\frac{{{i + 1}}}{{2}}$</p>")
                )
                with lock:
                    outputs[i] = self._text(pdf_bytes)
            except Exception as exc:  # pragma: no cover - failure path only
                with lock:
                    errors.append((i, repr(exc)))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(count)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        elapsed = time.perf_counter() - start
        return outputs, errors, elapsed

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=4)
    def test_concurrent_renders_do_not_contaminate_each_other(self):
        """
        Several pages are open in one browser at once. Each render must
        come back with its OWN document and nothing from any other -
        the correctness risk that concurrency introduces.
        """
        outputs, errors, _ = self._render_many(8)

        self.assertEqual(errors, [])
        self.assertEqual(len(outputs), 8)
        for i, text in outputs.items():
            self.assertIn(f"JOB{i}", text)
            for other in range(8):
                if other != i:
                    self.assertNotIn(f"JOB{other}:", text)
            # Math still typeset per page, not left as raw LaTeX.
            self.assertNotIn(r"\frac", text)

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=4)
    def test_renders_actually_overlap(self):
        """
        The point of the async model: 8 renders across 4 slots must take
        meaningfully less wall-clock than 8 serialized renders would.
        Compared against a measured single render rather than a fixed
        number, so this doesn't become a flaky machine-speed assertion.
        """
        solo_start = time.perf_counter()
        pdf_renderer.render_html_to_pdf(self._full_html("<p>warm</p>"))
        solo = time.perf_counter() - solo_start

        _, errors, elapsed = self._render_many(8)
        self.assertEqual(errors, [])

        # Fully serialized would be ~8x solo. Anything below 5x proves
        # real overlap while leaving generous headroom for a slow or
        # loaded CI machine.
        self.assertLess(
            elapsed,
            solo * 5,
            f"8 renders took {elapsed:.2f}s vs {solo:.2f}s for one - "
            "renders do not appear to be overlapping",
        )

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=1)
    def test_concurrency_of_one_still_renders_everything_correctly(self):
        """
        A concurrency bound of 1 restores fully serialized rendering.
        It must still be correct - this is the fallback if concurrency
        ever needs to be switched off in production.
        """
        outputs, errors, _ = self._render_many(4)

        self.assertEqual(errors, [])
        self.assertEqual(len(outputs), 4)
        for i, text in outputs.items():
            self.assertIn(f"JOB{i}", text)

    @override_settings(
        PDF_RENDERER_MAX_CONCURRENT_RENDERS=4,
        PDF_RENDERER_MAX_RENDERS_PER_BROWSER=3,
    )
    def test_recycling_while_renders_are_in_flight_loses_nothing(self):
        """
        The nastiest interaction in this module: the browser is swapped
        every 3 renders *while* up to 4 renders are in flight. A browser
        closed underneath an in-flight page would fail or truncate that
        render, so _acquire_browser drains in-flight work before closing.
        """
        outputs, errors, _ = self._render_many(12)

        self.assertEqual(errors, [])
        self.assertEqual(len(outputs), 12)
        for i, text in outputs.items():
            self.assertIn(f"JOB{i}", text)

    @override_settings(
        PDF_RENDERER_MAX_CONCURRENT_RENDERS=4,
        PDF_RENDERER_MAX_RENDERS_PER_BROWSER=3,
    )
    def test_one_slow_render_does_not_stall_the_others(self):
        """
        Regression guard for a real stall this stress-testing found: an
        earlier version waited for in-flight renders to drain *while
        holding the swap lock*, so every render queued behind the slowest
        one. Measured, a single hung render dragged nine ~0.2s renders out
        to ~5s each.

        A document whose __katexDone never becomes true waits out its full
        timeout. Healthy renders alongside it must still finish promptly -
        they may be slowed by sharing a concurrency slot, but must not be
        pinned to the hung render's timeout.
        """
        hung_timeout = 5.0
        hung_html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>t</title>"
            "</head><body><p>HUNG</p><script>"
            "Object.defineProperty(window,'__katexDone',"
            "{get:function(){return false;}});"
            "</script></body></html>"
        )

        durations = {}
        errors = []
        lock = threading.Lock()

        def render_hung():
            try:
                pdf_renderer.render_html_to_pdf(hung_html, timeout=hung_timeout)
            except pdf_renderer.PDFRenderError:
                pass  # expected: it never finishes typesetting

        def render_healthy(i):
            started = time.perf_counter()
            try:
                pdf_renderer.render_html_to_pdf(self._full_html(f"<p>FAST{i}</p>"))
                with lock:
                    durations[i] = time.perf_counter() - started
            except Exception as exc:  # pragma: no cover - failure path only
                with lock:
                    errors.append((i, repr(exc)))

        threads = [threading.Thread(target=render_hung)]
        threads += [
            threading.Thread(target=render_healthy, args=(i,)) for i in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [])
        self.assertEqual(len(durations), 6)
        # Comfortably under the hung render's timeout: if the stall
        # regressed, these would all sit at ~hung_timeout.
        self.assertLess(
            max(durations.values()),
            hung_timeout * 0.8,
            f"healthy renders were dragged out by the hung one: {durations}",
        )

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=2)
    def test_a_render_killed_by_a_dying_browser_is_retried_once(self):
        """
        Every render in flight when Chromium dies fails together, for a
        reason that has nothing to do with the document. One retry on a
        fresh browser turns that into a slower download rather than a
        failed one.
        """
        worker = pdf_renderer._get_worker()
        real_do_render = worker._do_render
        calls = {"n": 0}

        async def dies_on_first_call(browser, html, options):
            calls["n"] += 1
            if calls["n"] == 1:
                # Close the browser out from under this render, the way a
                # crash would, then fail the way _do_render would.
                await browser.close()
                raise pdf_renderer.PDFRenderError(
                    "PDF rendering failed: Target page, context or browser "
                    "has been closed"
                )
            return await real_do_render(browser, html, options)

        with patch.object(worker, "_do_render", dies_on_first_call):
            pdf_bytes = pdf_renderer.render_html_to_pdf(
                self._full_html("<p>SURVIVOR</p>")
            )

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertIn("SURVIVOR", self._text(pdf_bytes))
        self.assertEqual(calls["n"], 2, "expected exactly one retry")

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=2)
    def test_an_ordinary_failure_is_not_retried(self):
        """
        The retry must stay narrow: a failure with a healthy browser (a
        timeout, a broken document) is a real failure and must surface
        immediately rather than being rendered a second time.
        """
        worker = pdf_renderer._get_worker()
        calls = {"n": 0}

        async def always_fails(browser, html, options):
            calls["n"] += 1
            raise pdf_renderer.PDFRenderError("PDF rendering timed out: nope")

        with patch.object(worker, "_do_render", always_fails):
            with self.assertRaises(pdf_renderer.PDFRenderError):
                pdf_renderer.render_html_to_pdf(self._full_html("<p>x</p>"))

        self.assertEqual(calls["n"], 1, "a healthy-browser failure must not retry")

    @override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=2)
    def test_semaphore_bounds_how_many_pages_are_open_at_once(self):
        """
        The semaphore is what keeps peak memory predictable, so assert it
        actually caps in-flight renders rather than trusting the setting
        is wired up. Observed through the worker's own in-flight counter.
        """
        worker = pdf_renderer._get_worker()
        peak = {"n": 0}
        real_do_render = worker._do_render

        async def watching_do_render(browser, html, options):
            peak["n"] = max(peak["n"], worker._in_flight)
            return await real_do_render(browser, html, options)

        with patch.object(worker, "_do_render", watching_do_render):
            _, errors, _ = self._render_many(8)

        self.assertEqual(errors, [])
        self.assertGreater(peak["n"], 1, "renders never overlapped at all")
        self.assertLessEqual(
            peak["n"], 2, f"in-flight renders exceeded the bound of 2 (saw {peak['n']})"
        )


class WorkerSingletonTest(SimpleTestCase):
    """
    These don't need Chromium themselves - they test the singleton
    plumbing around it - but resetting the singleton will tear down a
    real browser if ChromiumRendererTest already started one, so keep
    them independent of test ordering by resetting before and after.
    """

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()

    def tearDown(self):
        pdf_renderer.reset_worker_for_tests()

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_get_worker_returns_the_same_instance_across_calls(self):
        first = pdf_renderer._get_worker()
        second = pdf_renderer._get_worker()
        self.assertIs(first, second)

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_reset_allows_a_fresh_instance_afterward(self):
        first = pdf_renderer._get_worker()
        pdf_renderer.reset_worker_for_tests()
        second = pdf_renderer._get_worker()
        self.assertIsNot(first, second)

    def test_get_worker_raises_a_clear_error_when_chromium_cannot_launch(self):
        # Simulate an unlaunchable browser without needing to actually
        # uninstall Chromium: monkeypatch async_playwright to something
        # that always fails, exactly as a missing/corrupt browser would.
        import unittest.mock as mock

        with mock.patch.object(pdf_renderer, "async_playwright") as mock_sp:
            mock_sp.side_effect = RuntimeError("simulated launch failure")
            with self.assertRaises(pdf_renderer.PDFRenderError):
                pdf_renderer._get_worker()
        # A failed launch must not leave a half-initialized singleton
        # behind for the next call to trip over.
        self.assertIsNone(pdf_renderer._worker)


class LoadSheddingTest(SimpleTestCase):
    """
    Refusing work past capacity instead of queueing it.

    Measured without this, 300 concurrent callers left renders sitting
    ~35s and 89 of 3000 died at the 45s timeout - each having pinned a
    request thread for the whole wait. Shedding converts that into an
    immediate, honest refusal.
    """

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()

    def tearDown(self):
        pdf_renderer.reset_worker_for_tests()

    def test_default_limit_is_four_times_the_concurrency(self):
        with override_settings(PDF_RENDERER_MAX_CONCURRENT_RENDERS=3):
            # Not configured -> derived, so the two stay in step if the
            # concurrency is retuned.
            with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=None):
                self.assertEqual(pdf_renderer._max_queued_renders(), 12)

    def test_explicit_limit_overrides_the_derived_default(self):
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=7):
            self.assertEqual(pdf_renderer._max_queued_renders(), 7)

    def test_ensure_capacity_is_a_no_op_with_no_renderer_running(self):
        # Must not start a browser just to answer "are you busy?" - a
        # process that has never rendered is not at capacity.
        self.assertIsNone(pdf_renderer._worker)
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=1):
            pdf_renderer.ensure_capacity()  # must not raise
        self.assertIsNone(pdf_renderer._worker, "should not have started a browser")

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_ensure_capacity_raises_once_at_the_limit(self):
        worker = pdf_renderer._get_worker()
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=2):
            worker._queued = 1
            pdf_renderer.ensure_capacity()  # under the limit: fine
            worker._queued = 2
            with self.assertRaises(pdf_renderer.PDFRendererBusy):
                pdf_renderer.ensure_capacity()
        worker._queued = 0

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_zero_disables_shedding(self):
        worker = pdf_renderer._get_worker()
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=0):
            worker._queued = 999
            pdf_renderer.ensure_capacity()  # must not raise
        worker._queued = 0

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_render_refuses_immediately_when_over_capacity(self):
        """
        The authoritative check lives in render() itself, so a caller that
        skips the advisory ensure_capacity() pre-check is still bounded.
        """
        worker = pdf_renderer._get_worker()
        html = (
            "<!doctype html><html><head><title>t</title></head>"
            "<body><p>hi</p></body></html>"
        )
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=1):
            worker._queued = 1
            started = time.perf_counter()
            with self.assertRaises(pdf_renderer.PDFRendererBusy):
                worker.render(html)
            elapsed = time.perf_counter() - started
        worker._queued = 0
        # Refusal must be immediate - the entire point is not to park the
        # caller's thread. Measured at ~0ms; 1s is a generous ceiling.
        self.assertLess(elapsed, 1.0)

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_a_shed_render_does_not_leak_a_queue_slot(self):
        worker = pdf_renderer._get_worker()
        html = (
            "<!doctype html><html><head><title>t</title></head>"
            "<body><p>hi</p></body></html>"
        )
        with override_settings(PDF_RENDERER_MAX_QUEUED_RENDERS=1):
            worker._queued = 1
            with self.assertRaises(pdf_renderer.PDFRendererBusy):
                worker.render(html)
            # The refused call never took a slot, so the count is untouched.
            self.assertEqual(worker._queued, 1)
        worker._queued = 0

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_the_queue_slot_is_released_after_a_successful_render(self):
        worker = pdf_renderer._get_worker()
        html = (
            "<!doctype html><html><head><title>t</title></head>"
            "<body><p>hi</p></body></html>"
        )
        pdf_renderer.render_html_to_pdf(html)
        self.assertEqual(worker._queued, 0)

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_the_queue_slot_is_released_after_a_failed_render(self):
        worker = pdf_renderer._get_worker()
        with patch.object(worker, "_await_render", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                worker.render("<html><head></head><body></body></html>")
        self.assertEqual(worker._queued, 0, "a failed render must free its slot")

    @unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
    def test_busy_is_a_pdfrendererror_so_existing_handlers_still_catch_it(self):
        self.assertTrue(
            issubclass(pdf_renderer.PDFRendererBusy, pdf_renderer.PDFRenderError)
        )
