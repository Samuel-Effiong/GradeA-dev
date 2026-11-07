"""Epic C1, FR-C-08: the one rule that decides when two tag names are the
same tag (plan 11 §3.2), with the owner's rulings of 2026-11-06.

1. Unicode NFKC: full-width letters, ligatures and compatibility forms
   become their plain form ("Ｆｒａｃｔｉｏｎｓ" is "Fractions").
2. Every whitespace character, a whitespace control (tab, newline, CR)
   included, becomes a space. Every other format (Cf) or control (Cc)
   character is removed (D5), so a zero-width space or a direction
   override can't make a second tag that looks the same.
3. The typographic apostrophe U+2019 becomes ' (the same approved mark).
4. Runs of spaces collapse to one, and the ends are trimmed. This is the
   display name: it keeps the teacher's casing. Empty: TAG_NAME_MISSING.
5. Only letters (L*), combining marks (M*), numbers (N*) and the marks in
   ALLOWED_MARKS may remain (the owner's visible-character rule, one row
   per class in CHARACTER_RULES). Anything else, symbols and emoji
   included: TAG_NAME_HAS_SYMBOLS.
6. Longer than MAX_NAME_LENGTH characters: TAG_NAME_TOO_LONG.
7. The key is the display name in lower case: `str.lower()`, not
   `casefold()` ("Straße" stays "straße").

The database holds the key unique per teacher and tag type.
"""

import unicodedata

from AutoGrader.reason_codes import CodedError

MAX_NAME_LENGTH = 100

#: One row per Unicode class: a two-letter category, or a major class by
#: its first letter. "other" is every class no row names.
CHARACTER_RULES = (
    ("Cf", "remove"),
    ("Cc", "remove"),
    ("L", "allow"),
    ("M", "allow"),
    ("N", "allow"),
    ("other", "refuse"),
)

#: The marks allowed beside letters and numbers (owner, 2026-11-06).
ALLOWED_MARKS = frozenset(" -'.()")

#: Written as the approved apostrophe.
_SAME_MARK = {"\u2019": "'"}

_RULES = dict(CHARACTER_RULES)


def _rule(character):
    category = unicodedata.category(character)
    return _RULES.get(category) or _RULES.get(category[0]) or _RULES["other"]


def _strip(character):
    if character.isspace():
        return " "
    if _rule(character) == "remove":
        return ""
    return _SAME_MARK.get(character, character)


def _allowed(character):
    return character in ALLOWED_MARKS or _rule(character) == "allow"


def clean_tag_name(typed):
    """Return `(display name, normalized key)`, or raise a CodedError, in
    this order: TAG_NAME_MISSING for anything that is not text or is empty
    once cleaned, TAG_NAME_HAS_SYMBOLS for a character the rule refuses,
    TAG_NAME_TOO_LONG past MAX_NAME_LENGTH."""
    if not isinstance(typed, str):
        raise CodedError("TAG_NAME_MISSING")
    text = unicodedata.normalize("NFKC", typed)
    display = " ".join("".join(_strip(c) for c in text).split())
    if not display:
        raise CodedError("TAG_NAME_MISSING")
    if not all(_allowed(c) for c in display):
        raise CodedError("TAG_NAME_HAS_SYMBOLS")
    key = display.lower()
    if max(len(display), len(key)) > MAX_NAME_LENGTH:
        raise CodedError("TAG_NAME_TOO_LONG")
    return display, key


def normalize_tag_name(typed):
    """The uniqueness key alone (see the module docstring)."""
    return clean_tag_name(typed)[1]
