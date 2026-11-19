"""Department names: one rule for what makes two names the same (owner Q9).

Same family as the tag rule (tags.normalize), without its coded errors:
NFKC, whitespace to single spaces, control and format characters removed,
the curly apostrophe made straight, ends trimmed. The key is the lower-cased
display name. Up to 100 characters, counted after cleaning.
"""

import unicodedata

MAX_NAME_LENGTH = 100


def clean_department_name(typed):
    """Return (display name, key). Either may be empty; the caller decides."""
    if not isinstance(typed, str):
        return "", ""
    text = unicodedata.normalize("NFKC", typed)
    kept = []
    for character in text:
        if character.isspace():
            kept.append(" ")
        elif character == "’":
            kept.append("'")
        elif unicodedata.category(character) in ("Cf", "Cc"):
            continue
        else:
            kept.append(character)
    display = " ".join("".join(kept).split())
    return display, display.lower()
