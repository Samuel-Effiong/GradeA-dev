"""Epic C1, FR-C-08: one normalization rule (plan 11 §3.2), with the
owner's D5 ruling of 2026-11-06: tag names hold no invisible characters.
Every Unicode format (Cf) and control (Cc) character is removed before the
trim and length checks; a whitespace control (tab, newline) becomes a space
first. Visible punctuation and emoji are kept as typed (pending the owner:
that rule is one row of `tags.normalize.CHARACTER_RULES`).
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

# Visible punctuation and emoji: kept as typed (plan 11; owner pending).
VISIBLE_KEPT = [
    ("C++ & Java!", "c++ & java!"),
    ("Cells \U0001f52c", "cells \U0001f52c"),
    ("Year 7: Fractions (Part 1)", "year 7: fractions (part 1)"),
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

    def test_visible_punctuation_and_emoji_are_kept(self):
        for typed, key in VISIBLE_KEPT:
            with self.subTest(typed=typed):
                self.assertEqual(normalize_tag_name(typed), key)

    def test_the_rule_is_one_table(self):
        # One row per character class, so the owner's pending ruling on
        # visible special characters is a one-row change.
        self.assertEqual(
            dict(CHARACTER_RULES),
            {"Cf": "remove", "Cc": "remove", "visible": "keep"},
        )
