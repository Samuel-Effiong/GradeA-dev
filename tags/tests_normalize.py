"""Epic C1, FR-C-08: one normalization rule (plan 11 §3.2), with the
owner's rulings of 2026-11-06.

D5: tag names hold no invisible characters. Every Unicode format (Cf) and
control (Cc) character is removed before the checks; a whitespace control
(tab, newline) becomes a space first.

The visible-character rule (replacing D5's "visible: keep" row): after the
strip and the collapse, a name may use only letters (L*), combining marks
(M*), numbers (N*), the space and - ' . ( ); the typographic apostrophe
U+2019 becomes '. Anything else is TAG_NAME_HAS_SYMBOLS. The order is:
empty (TAG_NAME_MISSING), then characters, then length.
"""

import importlib

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

# Owner rule of 2026-11-06 (replaces D5's "visible: keep" row): after the
# Cf/Cc strip and the collapse, only letters (L*), combining marks (M*),
# numbers (N*), the space and - ' . ( ) are allowed. The typographic
# apostrophe U+2019 is the same approved mark and becomes '.
# (typed, display name kept, normalized key)
ALLOWED = [
    ("Newton\u2019s Laws", "Newton's Laws", "newton's laws"),
    ("Grade 10 (Advanced)", "Grade 10 (Advanced)", "grade 10 (advanced)"),
    ("Ch. 3", "Ch. 3", "ch. 3"),
    ("Cell-Biology", "Cell-Biology", "cell-biology"),
    ("\u00c9nergie", "\u00c9nergie", "\u00e9nergie"),
    ("E\u0301nergie", "\u00c9nergie", "\u00e9nergie"),  # combining accent, NFKC
    (
        "\u0930\u0938\u093e\u092f\u0928",
        "\u0930\u0938\u093e\u092f\u0928",
        "\u0930\u0938\u093e\u092f\u0928",
    ),
    (
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
        "\u0627\u0644\u0643\u064a\u0645\u064a\u0627\u0621",
    ),
    ("\uff08Part 1\uff09", "(Part 1)", "(part 1)"),  # full-width ( ) are ( ) after NFKC
]

# One case per refused class (the SM's list), each with the owner's message.
REFUSED = [
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
    "\u201cQuoted\u201d",  # typographic double quotes
    "\u2018Single\u2019",  # U+2018 is not the approved apostrophe
    "Cells \u2013 Tissues",  # en dash
    "Cells \u2014 Tissues",  # em dash
    "Cell\u2010Biology",  # Unicode hyphen, not -
    "\u00a9 Science",  # Unicode symbol (So)
    "Cost $5",  # currency symbol (Sc)
    "x^2",  # modifier symbol (Sk)
    "a < b",  # math symbol (Sm)
    "a ~ b",
    "Back`tick",
    "Pipe|Line",
    "Cells \U0001f52c",  # emoji
    "Family \U0001f468\u200d\U0001f469\u200d\U0001f467",  # ZWJ family
    "\U0001f600",  # an emoji alone is not missing: it is refused
    # NFKC turns \u00bd into 1, U+2044, 2, and U+2044 FRACTION SLASH is a math
    # symbol (Sm), so a vulgar fraction is refused (flagged to the SM).
    "Year \u00bd",
]

HAS_SYMBOLS = "Tag names can only use letters, numbers, spaces and - ' . ( )"


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

    def test_the_allowed_characters_are_kept(self):
        for typed, shown, key in ALLOWED:
            with self.subTest(typed=typed):
                self.assertEqual(clean_tag_name(typed), (shown, key))

    def test_every_other_visible_character_is_refused(self):
        for typed in REFUSED:
            with self.subTest(typed=typed):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, "TAG_NAME_HAS_SYMBOLS")
                self.assertEqual(str(caught.exception), HAS_SYMBOLS)

    def test_the_order_missing_then_symbols_then_length(self):
        # Empty after the strip is missing, even with a stripped ZWJ.
        for typed, code in (
            ("\u200d", "TAG_NAME_MISSING"),
            ("#" * 101, "TAG_NAME_HAS_SYMBOLS"),
            ("a" * 100 + "!", "TAG_NAME_HAS_SYMBOLS"),
            ("a" * 101, "TAG_NAME_TOO_LONG"),
        ):
            with self.subTest(typed=typed[:12]):
                with self.assertRaises(CodedError) as caught:
                    clean_tag_name(typed)
                self.assertEqual(caught.exception.reason_code, code)

    def test_the_rule_is_one_table(self):
        # One row per Unicode class, plus the five approved marks.
        self.assertEqual(
            dict(CHARACTER_RULES),
            {
                "Cf": "remove",
                "Cc": "remove",
                "L": "allow",
                "M": "allow",
                "N": "allow",
                "other": "refuse",
            },
        )
        # Looked up at run time: not on the base when this was written.
        normalize = importlib.import_module("tags.normalize")
        self.assertEqual(normalize.ALLOWED_MARKS, frozenset(" -'.()"))
