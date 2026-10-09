"""
Coverage for evidence.py's LaTeX-cosmetic desugaring and ellipsis-
fragment splitting (see the module docstring in ai_processor/evidence.py
for the full reasoning).

Where this came from: a live benchmark run (FINDINGS.md, Run 4) showed
evidence-verified dropping for three runs straight, concentrated almost
entirely in Chemistry (75% verified vs 97.6% for Mathematics and 100%
for History & Literature, which has no LaTeX at all). The actual model
quotes were pulled from the recorded run and compared to what the
student wrote: the grades were correct throughout, but the model kept
re-typesetting its own quotes while composing them — dropping
subscripts, turning `\\frac{a}{b}` into "a/b", swapping `\\rightarrow`
for "→" — which is faithful to the MEANING but fails a literal string
match. TestRealBenchmarkQuotesVerify below uses that exact captured
data (21 of 23 previously-failing real quotes now verify; the other
2 are documented, deliberately unfixed gaps) as regression coverage,
so a rewrite of this module has to keep working on real cases, not
just the synthetic ones below.

Run with:
    python manage.py test ai_processor.tests_evidence_latex
"""

from django.test import SimpleTestCase

from ai_processor.evidence import quote_appears_in_answer


class SubscriptSuperscriptTest(SimpleTestCase):
    def test_bare_subscript_dropped_by_the_model(self):
        # answer: "[H_2]" (LaTeX), model quotes "[H2]" (subscript dropped)
        self.assertTrue(quote_appears_in_answer("[H2] = 0.100", "$[H_2] = 0.100$"))

    def test_braced_subscript(self):
        self.assertTrue(quote_appears_in_answer("Ca2+", r"$Ca_{2+}$"))

    def test_bare_superscript_dropped_by_the_model(self):
        self.assertTrue(quote_appears_in_answer("x2", r"$x^2$"))

    def test_unicode_superscript_vs_latex_caret(self):
        # model renders as a literal unicode superscript digit
        self.assertTrue(quote_appears_in_answer("Kc = x²", r"$K_c = x^2$"))

    def test_unicode_superscript_minus_vs_ascii_hyphen(self):
        # NFKC decomposes U+207B into MINUS SIGN (U+2212), not ASCII '-';
        # the LaTeX source's bare "^-" keeps the literal ASCII hyphen.
        # Both must fold to the same character or this never matches.
        self.assertTrue(
            quote_appears_in_answer("k[RX][Nu⁻]", r"rate $= k[\text{RX}][\text{Nu}^-]$")
        )


class TextWrapperTest(SimpleTestCase):
    def test_text_wrapper_stripped(self):
        self.assertTrue(quote_appears_in_answer("k[RX]", r"rate $= k[\text{RX}]$"))

    def test_mathrm_wrapper_stripped(self):
        self.assertTrue(quote_appears_in_answer("d = 5 m", r"$d = 5 \mathrm{m}$"))


class FracExpansionTest(SimpleTestCase):
    def test_simple_fraction_expanded(self):
        self.assertTrue(
            quote_appears_in_answer(
                "0.200/2.00 = 0.100", r"$\frac{0.200}{2.00} = 0.100$"
            )
        )

    def test_fraction_with_parenthesized_operands(self):
        self.assertTrue(
            quote_appears_in_answer(
                "(0.800)² / (0.100)(0.100)", r"$\frac{(0.800)^2}{(0.100)(0.100)}$"
            )
        )

    def test_nested_fraction_expanded(self):
        self.assertTrue(quote_appears_in_answer("1/2/3", r"$\frac{1}{\frac{2}{3}}$"))

    def test_ambiguous_numerator_left_untouched(self):
        # "(a+1)/b" is unambiguous; "a+1/b" is not (normal precedence
        # reads that as "a + (1/b)"). A quote using bare division here
        # must NOT verify against the LaTeX fraction — that would be
        # equating two expressions that read differently as plain text.
        self.assertFalse(quote_appears_in_answer("a+1/b = 4", r"$\frac{a+1}{b} = 4$"))

    def test_ambiguous_denominator_left_untouched(self):
        self.assertFalse(quote_appears_in_answer("a/b-c = 4", r"$\frac{a}{b-c} = 4$"))

    def test_leading_minus_is_a_sign_not_an_operator(self):
        # A leading '-' is part of a negative number, not a binary
        # operator, so this stays eligible for expansion.
        self.assertTrue(quote_appears_in_answer("-393.5/2", r"$\frac{-393.5}{2}$"))

    def test_frac_not_present_is_a_noop(self):
        self.assertFalse(quote_appears_in_answer("a/b", "plain prose with no math"))


class SymbolSynonymTest(SimpleTestCase):
    def test_rightarrow_vs_unicode_arrow(self):
        self.assertTrue(
            quote_appears_in_answer(
                "CH4 + 2O2 → CO2 + 2H2O", r"$CH_4 + 2O_2 \rightarrow CO_2 + 2H_2O$"
            )
        )

    def test_delta_command_with_space_vs_model_without_space(self):
        # "\Delta H" (space required by LaTeX to terminate the macro
        # name) and "ΔH" (model drops it) mean the same thing.
        self.assertTrue(quote_appears_in_answer("ΔH = -604.5", r"$\Delta H = -604.5$"))

    def test_arrow_with_space_preserved_when_model_keeps_it(self):
        # The opposite spacing choice must ALSO still match — the model
        # is inconsistent about which one it uses per quote.
        self.assertTrue(
            quote_appears_in_answer("CO2 + 2H2O", r"$\rightarrow CO_2 + 2H_2O$")
        )

    def test_times_and_cdot(self):
        self.assertTrue(quote_appears_in_answer("2 × 3", r"$2 \times 3$"))
        self.assertTrue(quote_appears_in_answer("2 · 3", r"$2 \cdot 3$"))

    def test_function_names_not_in_symbol_table(self):
        # \ln, \sin, \log etc. are deliberately NOT in the symbol
        # synonym list — a dropped backslash there is a different,
        # unobserved failure mode, and guessing at it isn't justified by
        # any real data. (There's no clean black-box test for "nothing
        # happens to \ln": stripping its backslash would make "ln(x)" a
        # substring of "\ln(x)" regardless, since \COMMAND always
        # textually contains COMMAND. Asserting on the table directly is
        # the honest way to pin this.)
        from ai_processor.evidence import _LATEX_SYMBOLS

        commands = {latex for latex, _glyph in _LATEX_SYMBOLS}
        self.assertTrue(
            commands.isdisjoint({r"\ln", r"\sin", r"\cos", r"\log", r"\exp"})
        )


class DollarDelimiterTest(SimpleTestCase):
    def test_dollar_signs_stripped_as_delimiters(self):
        self.assertTrue(
            quote_appears_in_answer(
                "[H2] = 0.100, [I2] = 0.100",
                "$[H_2] = 0.100$, $[I_2] = 0.100$",
            )
        )

    def test_display_math_delimiters_stripped(self):
        self.assertTrue(quote_appears_in_answer("x = 5", "$$x = 5$$"))


class SlashSpacingTest(SimpleTestCase):
    def test_spaced_slash_matches_bare_slash(self):
        self.assertTrue(quote_appears_in_answer("2.56/0.04", r"$\frac{2.56}{0.04}$"))
        # model renders the same fraction with spaces around the slash
        self.assertTrue(quote_appears_in_answer("2.56 / 0.04", r"$\frac{2.56}{0.04}$"))


class EllipsisFragmentSplittingTest(SimpleTestCase):
    ANSWER = (
        r"rate $= k[\text{RX}]$. $S_N2$ is concerted, so "
        r"rate $= k[\text{RX}][\text{Nu}^-]$."
    )

    def test_ellipsis_joined_quote_splits_and_each_fragment_verifies(self):
        self.assertTrue(
            quote_appears_in_answer("rate = k[RX] ... rate = k[RX][Nu⁻]", self.ANSWER)
        )

    def test_unicode_ellipsis_character_also_splits(self):
        self.assertTrue(
            quote_appears_in_answer("rate = k[RX] … rate = k[RX][Nu⁻]", self.ANSWER)
        )

    def test_all_fragments_must_verify_not_just_one(self):
        self.assertFalse(
            quote_appears_in_answer(
                "rate = k[RX] ... this part is made up", self.ANSWER
            )
        )

    def test_ellipsis_only_quote_has_no_real_fragments(self):
        self.assertFalse(quote_appears_in_answer("...", self.ANSWER))

    def test_quote_without_ellipsis_is_unaffected(self):
        # No regression on the ordinary single-span path.
        self.assertTrue(quote_appears_in_answer("rate = k[RX]", self.ANSWER))
        self.assertFalse(quote_appears_in_answer("made up nonsense", self.ANSWER))


class ProseSafetyTest(SimpleTestCase):
    """
    The whole safety argument for this module: none of the above may
    loosen matching for ordinary prose. LaTeX commands and math
    delimiters essentially never occur in genuine essay answers, so
    every transformation above should be a no-op there.
    """

    def test_not_able_vs_notable_still_distinct(self):
        self.assertFalse(quote_appears_in_answer("not able", "notable to attend"))

    def test_paraphrase_still_does_not_verify(self):
        self.assertFalse(
            quote_appears_in_answer(
                "turns sunlight into usable energy",
                "<p>Photosynthesis converts light energy into chemical energy.</p>",
            )
        )

    def test_currency_dollar_signs_do_not_corrupt_spacing(self):
        # A regression guard on an earlier draft of this fix: collapsing
        # ALL whitespace between two '$' characters treated "$5 not $50"
        # as one math span and corrupted the prose. Delimiter-stripping
        # alone must not do that.
        self.assertTrue(
            quote_appears_in_answer("price was $5 not $50", "The price was $5 not $50.")
        )

    def test_slash_in_prose_still_matches_normally(self):
        self.assertTrue(
            quote_appears_in_answer("input/output", "the input/output ratio")
        )


class TestRealBenchmarkQuotesVerify(SimpleTestCase):
    """
    Real (quote, answer) pairs captured from the live Run 4 benchmark —
    see ai_processor/benchmark/FINDINGS.md. Every one of these was a
    correct grade that the OLD verifier rejected as "fabricated" purely
    because the model re-typeset the LaTeX while quoting it. Pinning the
    actual production strings (not paraphrased-for-readability versions)
    means a future refactor of this module is checked against the exact
    failures that motivated it, not an idealized version of them.
    """

    def test_dropped_subscripts_across_a_compound_quote(self):
        quote = (
            "[H2] = 0.200/2.00 = 0.100, [I2] = 0.100, [HI] = 1.60/2.00 = "
            "0.800 mol dm⁻³."
        )
        answer = (
            "<p>Concentrations: $[H_2] = \\frac{0.200}{2.00} = 0.100$, "
            "$[I_2] = 0.100$, $[HI] = \\frac{1.60}{2.00} = 0.800$ "
            "mol dm⁻³.</p>"
        )
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_kc_with_unicode_superscript_and_bare_division(self):
        quote = (
            "Kc = [HI]² / [H2][I2] = (0.800)² / (0.100)(0.100) = 0.640 / 0.0100 = 64.0."
        )
        answer = (
            "<p>$K_c = \\frac{[HI]^2}{[H_2][I_2]} = \\frac{(0.800)^2}"
            "{(0.100)(0.100)} = \\frac{0.640}{0.0100} = 64.0$.</p>"
        )
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_combustion_equation_arrow_and_subscripts(self):
        quote = "CH4 + 2O2 → CO2 + 2H2O"
        answer = (
            "<p>Burning methane completely gives one carbon dioxide and "
            "two waters per methane: $CH_4 + 2O_2 \\rightarrow CO_2 + "
            "2H_2O$.</p>"
        )
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_delta_h_no_space_vs_source_with_space(self):
        quote = "ΔH = -393.5 + (-285.8) - (-74.8) = -604.5 kJ/mol."
        answer = "<p>$\\Delta H = -393.5 + (-285.8) - (-74.8) = -604.5$ " "kJ/mol.</p>"
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_ellipsis_joined_rate_law_quote(self):
        quote = "rate = k[RX] ... rate = k[RX][Nu⁻]"
        answer = (
            "<p><b>Rate.</b> $S_N1$ proceeds through a rate-determining "
            "ionisation to a carbocation; since the nucleophile enters "
            "only after that step, it cannot appear in the rate law, "
            "giving rate $= k[\\text{RX}]$. $S_N2$ is concerted — bond "
            "forming and bond breaking occur in one transition state "
            "containing both species — so rate $= k[\\text{RX}]"
            "[\\text{Nu}^-]$.</p>"
        )
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_stripped_underscores_on_sn1_sn2(self):
        quote = (
            "SN1 has one step and SN2 has two steps. In SN1 the rate "
            "only depends on the halogenoalkane, and in SN2 it depends "
            "on both reactants."
        )
        answer = (
            "<p>$S_N1$ has one step and $S_N2$ has two steps. In $S_N1$ "
            "the rate only depends on the halogenoalkane, and in $S_N2$ "
            "it depends on both reactants. $S_N1$ happens with tertiary "
            "and $S_N2$ with primary.</p>"
        )
        self.assertTrue(quote_appears_in_answer(quote, answer))

    def test_documented_remaining_gap_cross_paragraph_quote(self):
        # NOT fixed by design: this quote spans a </p><p> boundary AND a
        # sentence-final period sitting directly against a </b> tag, a
        # pre-existing tag-boundary quirk unrelated to LaTeX. A single
        # narrow instance in the benchmark; the degrade-on-final-retry
        # safety net (services.py) still gets this submission a grade.
        quote = (
            "Only 0.0500 mol is available, so HCl is limiting. "
            "n(CO_2) = 0.0500/2 = 0.0250 mol, so m(CO_2) = 0.0250 × "
            "44.0 = 1.10 g."
        )
        answer = (
            "<p>The equation needs 2 mol HCl per mol $CaCO_3$, so "
            "$0.0500$ mol $CaCO_3$ would require $0.100$ mol HCl. Only "
            "$0.0500$ mol is available, so <b>HCl is limiting</b>.</p>"
            "<p>$n(CO_2) = \\frac{0.0500}{2} = 0.0250$ mol, so "
            "$m(CO_2) = 0.0250 \\times 44.0 = 1.10$ g.</p>"
        )
        self.assertFalse(quote_appears_in_answer(quote, answer))

    def test_documented_remaining_gap_model_adds_protective_parens(self):
        # NOT fixed by design: the model wrapped a multiplied denominator
        # in extra parentheses ("(0.200 × 0.200)") that the LaTeX source
        # never had. Reasonable of the model (it preserves order of
        # operations for a plain-text reading) but this module does not
        # try to guess where a human would insert protective parens —
        # doing so reliably is a much larger undertaking than the
        # bounded, evidence-driven scope of this fix.
        quote = (
            "Kc = [HI]^2 / [H2][I2]. Using the moles directly: "
            "1.60^2 / (0.200 × 0.200) = 2.56/0.04 = 64."
        )
        answer = (
            "<p>$K_c = \\frac{[HI]^2}{[H_2][I_2]}$. Using the moles "
            "directly: $\\frac{1.60^2}{0.200 \\times 0.200} = "
            "\\frac{2.56}{0.04} = 64$.</p>"
        )
        self.assertFalse(quote_appears_in_answer(quote, answer))
