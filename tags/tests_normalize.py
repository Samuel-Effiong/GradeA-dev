"""Epic C1, FR-C-08: one normalization rule (plan 11 §3.2), with the
owner's D5 ruling of 2026-11-06: tag names hold no invisible characters.
Every Unicode format (Cf) and control (Cc) character is removed before the
trim and length checks; a whitespace control (tab, newline) becomes a space
first.

Owner, 2026-11-08: the visible-character rule of 2026-11-06 is reversed.
Visible symbols, punctuation and emoji are kept as typed (NFKC applied);
there is no TAG_NAME_HAS_SYMBOLS.
"""

from django.test import SimpleTestCase

from AutoGrader.reason_codes import CodedError
from tags.normalize import (
    CHARACTER_RULES,
    MAX_NAME_LENGTH,
    clean_tag_name,
    normalize_tag_name,
)

# (typed, normalized key, display name kept)
SAME_TAG = [
    ("Fractions", "fractions", "Fractions"),
    ("fractions ", "fractions", "fractions"),
    ("FRACTIONS", "fractions", "FRACTIONS"),
    ("  Cell   Biology  ", "cell biology", "Cell Biology"),
    ("Cell\tBiology", "cell biology", "Cell Biology"),
    ("Cell\nBiology", "cell biology", "Cell Biology"),
    ("Cell\u00a0Biology", "cell biology", "Cell Biology"),  # NBSP
    ("Cell\u3000Biology", "cell biology", "Cell Biology"),  # ideographic space
    (
        "\uff26\uff52\uff41\uff43\uff54\uff49\uff4f\uff4e\uff53",
        "fractions",
        "Fractions",
    ),
    ("\ufb01ssion", "fission", "fission"),  # the "fi" ligature, NFKC
    ("Ökologie", "\u00f6kologie", "Ökologie"),
]

# D5 (owner, 2026-11-06): invisible characters are removed.
INVISIBLE = [
    ("Frac\u200btions", "fractions", "Fractions"),  # ZERO WIDTH SPACE
    ("Frac\u200ctions", "fractions", "Fractions"),  # ZERO WIDTH NON-JOINER
    ("Frac\u200dtions", "fractions", "Fractions"),  # ZERO WIDTH JOINER
    ("Frac\u2060tions", "fractions", "Fractions"),  # WORD JOINER
    ("\ufeffFractions", "fractions", "Fractions"),  # BYTE ORDER MARK
    ("Frac\u202etions", "fractions", "Fractions"),  # RIGHT-TO-LEFT OVERRIDE
    ("\u200eFractions\u200f", "fractions", "Fractions"),  # LRM / RLM
    ("\x07Fractions", "fractions", "Fractions"),  # a lone control (BEL)
    ("Frac\x00tions", "fractions", "Fractions"),  # NUL
    ("Frac\x7ftions", "fractions", "Fractions"),  # DEL
    ("Cell\rBiology", "cell biology", "Cell Biology"),  # CR: whitespace, a space
    (" \u200b Cells \u2060 ", "cells", "Cells"),  # removed before the trim
]

# Only invisible characters: nothing left, so the name is missing.
ONLY_INVISIBLE = ["\u200b", "\u200b\u2060\ufeff", "\x07", "\u202e\u200d", "\t\u200b\n"]

# Owner, 2026-11-08: every visible character is kept as typed, after NFKC.
# (typed, display name kept, normalized key)
VISIBLE_KEPT = [
    ("Reading & Writing", "Reading & Writing", "reading & writing"),
    ("C#", "C#", "c#"),
    # NFKC turns \u00bd into 1, U+2044 FRACTION SLASH, 2.
    ("Year \u00bd", "Year 1\u20442", "year 1\u20442"),
    ("Cells \U0001f52c", "Cells \U0001f52c", "cells \U0001f52c"),
    ("\U0001f600", "\U0001f600", "\U0001f600"),  # an emoji alone is a name
    # D5 still removes the zero-width joiners (Cf) between the emoji.
    (
        "Family \U0001f468\u200d\U0001f469\u200d\U0001f467",
        "Family \U0001f468\U0001f469\U0001f467",
        "family \U0001f468\U0001f469\U0001f467",
    ),
    # Owner, 2026-11-08: U+2019 is the straight apostrophe.
    ("Newton\u2019s Laws", "Newton's Laws", "newton's laws"),
    ("Grade 10 (Advanced)", "Grade 10 (Advanced)", "grade 10 (advanced)"),
    ("\u00c9nergie", "\u00c9nergie", "\u00e9nergie"),
    ("E\u0301nergie", "\u00c9nergie", "\u00e9nergie"),  # combining accent, NFKC
    (
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
    ),
    ("\uff08Part 1\uff09", "(Part 1)", "(part 1)"),  # full-width ( ) after NFKC
]

# The names the 2026-11-06 rule refused: each is now kept exactly as typed
# (none changes under NFKC).
NO_LONGER_REFUSED = [
    "C# Basics",
    "Q&A",
    "Email @ School",
    "Wow!",
    "Either/Or",
    "Cells, Tissues",
    "Year 7: Fractions",
    "Part 1; Part 2",
    "snake_case",
    "C++",
    "a = b",
    "Why?",
    "Star*",
    '"Quoted"',
    "\u201cQuoted\u201d",
    "Cells \u2013 Tissues",
    "Cells \u2014 Tissues",
    "Cell\u2010Biology",
    "\u00a9 Science",
    "Cost $5",
    "x^2",
    "a < b",
    "a ~ b",
    "Back`tick",
    "Pipe|Line",
]


# Owner, 2026-11-08: U+2019 only becomes '. Every other quote and
# look-alike is kept as typed. (typed, display name kept)
ONLY_U2019_MAPPED = [
    ("\u2018Single\u2019", "\u2018Single'"),  # U+2018 kept, U+2019 mapped
    ("\u201cQuoted\u201d", "\u201cQuoted\u201d"),  # double quotes kept
    ("Hawai\u02bbi", "Hawai\u02bbi"),  # U+02BB turned comma
    ("Newton\u02bcs Laws", "Newton\u02bcs Laws"),  # U+02BC modifier apostrophe
    ("Angle 30\u2032", "Angle 30\u2032"),  # U+2032 prime
    ("Newton`s Laws", "Newton`s Laws"),  # backtick
]


class NormalizeTest(SimpleTestCase):
    def test_variants_resolve_to_one_key(self):
        for typed, key, _ in SAME_TAG:
            with self.subTest(typed=typed):
                self.assertEqual(normalize_tag_name(typed), key)

    def test_the_display_name_keeps_the_teachers_casing(self):
        for typed, key, shown in SAME_TAG:
            with self.subTest(typed=typed):
                self.assertEqual(clean_tag_name(typed), (shown, key))

    def test_lower_not_casefold(self):
        # 03a and D5: lower(). casefold() would turn "ß" into "ss".
        self.assertEqual(normalize_tag_name("Straße"), "straße")

    def test_an_empty_name_is_refused(self):
        for typed in ("", "   ", "\t\n", "\u00a0\u3000"):
            with self.subTest(typed=repr(typed)):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, "TAG_NAME_MISSING")

    def test_a_name_not_text_is_refused_as_missing(self):
        for typed in (None, 7, ["Cells"]):
            with self.subTest(typed=typed):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, "TAG_NAME_MISSING")

    def test_the_length_limit(self):
        self.assertEqual(MAX_NAME_LENGTH, 100)
        self.assertEqual(clean_tag_name("a" * 100)[1], "a" * 100)
        # Counted after trimming: surrounding spaces do not count.
        self.assertEqual(clean_tag_name("  " + "a" * 100 + "  ")[1], "a" * 100)
        for typed in ("a" * 101, "a " * 51):
            with self.subTest(length=len(typed)):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, "TAG_NAME_TOO_LONG")

    def test_the_length_limit_applies_after_nfkc_expansion(self):
        # 99 ligatures display as 99 characters but normalize to 198.
        with self.assertRaises(CodedError) as caught:
            clean_tag_name("\ufb01" * 99)
        self.assertEqual(caught.exception.reason_code, "TAG_NAME_TOO_LONG")

    def test_invisible_characters_are_removed(self):
        for typed, key, shown in INVISIBLE:
            with self.subTest(typed=repr(typed)):
                self.assertEqual(clean_tag_name(typed), (shown, key))

    def test_a_name_of_only_invisible_characters_is_missing(self):
        for typed in ONLY_INVISIBLE:
            with self.subTest(typed=repr(typed)):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, "TAG_NAME_MISSING")

    def test_invisible_characters_do_not_count_toward_the_limit(self):
        self.assertEqual(clean_tag_name("a" * 100 + "\u200b" * 20)[1], "a" * 100)

    def test_visible_symbols_punctuation_and_emoji_are_kept(self):
        for typed, shown, key in VISIBLE_KEPT:
            with self.subTest(typed=typed):
                self.assertEqual(clean_tag_name(typed), (shown, key))

    def test_the_names_the_old_rule_refused_are_kept_as_typed(self):
        for typed in NO_LONGER_REFUSED:
            with self.subTest(typed=typed):
                self.assertEqual(clean_tag_name(typed), (typed, typed.lower()))

    def test_the_order_missing_then_length(self):
        # Empty after the strip is missing, even with a stripped ZWJ.
        for typed, code in (
            ("\u200d", "TAG_NAME_MISSING"),
            ("#" * 101, "TAG_NAME_TOO_LONG"),
            ("a" * 100 + "!", "TAG_NAME_TOO_LONG"),
            ("a" * 101, "TAG_NAME_TOO_LONG"),
        ):
            with self.subTest(typed=typed[:12]):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, code)

    def test_the_rule_is_one_table(self):
        # One row per character class (D5); the 2026-11-08 reversal is the
        # "visible" row back to "keep".
        self.assertEqual(
            dict(CHARACTER_RULES),
            {"Cf": "remove", "Cc": "remove", "visible": "keep"},
        )


class CurlyApostropheTest(SimpleTestCase):
    """Owner, 2026-11-08: a tag typed with U+2019 is the same tag as one
    typed with a straight apostrophe, and it is stored with the straight
    one."""

    def test_curly_and_straight_are_one_tag(self):
        straight = clean_tag_name("Newton's Laws")
        for typed in (
            "Newton\u2019s Laws",
            "NEWTON\u2019S LAWS",
            " Newton\u2019s  Laws ",
        ):
            with self.subTest(typed=typed):
                self.assertEqual(normalize_tag_name(typed), straight[1])
        self.assertEqual(clean_tag_name("Newton\u2019s Laws"), straight)

    def test_every_u2019_is_mapped(self):
        self.assertEqual(
            clean_tag_name("Rock \u2019n\u2019 Roll\u2019s"),
            ("Rock 'n' Roll's", "rock 'n' roll's"),
        )

    def test_the_mapping_does_not_change_the_length(self):
        self.assertEqual(clean_tag_name("\u2019" * 100)[1], "'" * 100)
        with self.assertRaises(CodedError) as caught:
            clean_tag_name("\u2019" * 101)
        self.assertEqual(caught.exception.reason_code, "TAG_NAME_TOO_LONG")

    def test_only_u2019_is_mapped(self):
        for typed, shown in ONLY_U2019_MAPPED:
            with self.subTest(typed=typed):
                self.assertEqual(clean_tag_name(typed), (shown, shown.lower()))
