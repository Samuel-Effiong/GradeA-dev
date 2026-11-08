"""Epic C1, FR-C-08: the one rule that decides when two tag names are the
same tag (plan 11 §3.2), with the owner's D5 ruling of 2026-11-06.

1. Unicode NFKC: full-width letters, ligatures and compatibility forms
   become their plain form ("Ｆｒａｃｔｉｏｎｓ" is "Fractions").
2. Every whitespace character, a whitespace control (tab, newline, CR)
   included, becomes a space.
3. Every other character is looked up in CHARACTER_RULES by its Unicode
   category: format (Cf) and control (Cc) characters are removed, so a
   zero-width space or a direction override can't make a second tag that
   looks the same. Visible characters, punctuation and emoji included, are
   kept as typed.
4. Runs of spaces collapse to one, and the ends are trimmed. This is the
   display name: it keeps the teacher's casing.
5. The key is the display name in lower case: `str.lower()`, not
   `casefold()` ("Straße" stays "straße").

An empty result is refused as missing; a display name or key longer than
MAX_NAME_LENGTH characters, counted after all of the above, is refused as
too long. The database holds the key unique per teacher and tag type.
"""

import unicodedata

from AutoGrader.reason_codes import CodedError

MAX_NAME_LENGTH = 100

#: One row per character class (D5). The owner's pending ruling on visible
#: special characters is a change to the "visible" row only.
CHARACTER_RULES = (
    ("Cf", "remove"),
    ("Cc", "remove"),
    ("visible", "keep"),
)

_RULES = dict(CHARACTER_RULES)


def _keep(character):
    if character.isspace():
        return " "
    rule = _RULES.get(unicodedata.category(character), _RULES["visible"])
    return character if rule == "keep" else ""


def clean_tag_name(typed):
    """Return `(display name, normalized key)`, or raise a CodedError:
    TAG_NAME_MISSING for anything that is not text or is empty once cleaned,
    TAG_NAME_TOO_LONG past MAX_NAME_LENGTH."""
    if not isinstance(typed, str):
        raise CodedError("TAG_NAME_MISSING")
    text = unicodedata.normalize("NFKC", typed)
    display = " ".join("".join(_keep(c) for c in text).split())
    if not display:
        raise CodedError("TAG_NAME_MISSING")
    key = display.lower()
    if max(len(display), len(key)) > MAX_NAME_LENGTH:
        raise CodedError("TAG_NAME_TOO_LONG")
    return display, key


def normalize_tag_name(typed):
    """The uniqueness key alone (see the module docstring)."""
    return clean_tag_name(typed)[1]
