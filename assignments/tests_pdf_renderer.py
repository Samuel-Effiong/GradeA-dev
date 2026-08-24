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
import unittest

import fitz
from django.test import SimpleTestCase

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
        # uninstall Chromium: monkeypatch sync_playwright to something
        # that always fails, exactly as a missing/corrupt browser would.
        import unittest.mock as mock

        with mock.patch.object(pdf_renderer, "sync_playwright") as mock_sp:
            mock_sp.side_effect = RuntimeError("simulated launch failure")
            with self.assertRaises(pdf_renderer.PDFRenderError):
                pdf_renderer._get_worker()
        # A failed launch must not leave a half-initialized singleton
        # behind for the next call to trip over.
        self.assertIsNone(pdf_renderer._worker)
