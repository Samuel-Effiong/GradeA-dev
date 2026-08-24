"""
Cache for rendered assignment PDFs.

Generating one of these is expensive - assembling the HTML, then a full
headless-Chromium render with KaTeX typesetting (see
assignments/pdf_renderer.py) - and the result is identical for everyone
who downloads the same unchanged assignment in the same view. A class of
thirty students downloading the same worksheet currently pays that cost
thirty times over for byte-identical output.

Keyed by assignment id + view type + the assignment's `updated_at`
timestamp. Putting the timestamp *in the key* means there is nothing to
invalidate by hand, the same design ai_processor/grading_cache.py uses
for the same reason: editing an assignment bumps `updated_at`
(auto_now=True, so every write path gets it for free), which changes the
key, so the next request is a natural miss under the new key and the
superseded entry just ages out under its TTL. A future write path added
without a matching invalidation hook therefore cannot serve a stale PDF.

The view type matters because the teacher's copy includes rubrics that
the student's must not (see `include_rubric` in
AssignmentViewSet.download_pdf) - the two must never share a cache entry.
Nothing else in the rendered document varies per requesting user, so
those three components are the whole key. Note the permission checks in
download_pdf run *before* any lookup here, so a cache hit can never
bypass them.

Storage is whatever django.core.cache.cache resolves to (Redis in every
deployed environment, see AutoGrader/settings.py CACHES). Every call is
wrapped: a cache backend hiccup degrades to "render it fresh", never to
a failed download.
"""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Namespaced under "assignments:" so the existing wildcard invalidation in
# assignments/signals.py (clear_assignment_cache, which deletes
# "assignments:*" on every Assignment save/delete) sweeps these too. That
# is belt-and-braces on top of the timestamped key, not the mechanism the
# correctness of this cache depends on.
CACHE_KEY_PREFIX = "assignments:pdf"
# Bump to invalidate every cached PDF at once (e.g. after a change to the
# PDF template/styling, which the key's own components can't detect).
CACHE_VERSION = "v1"


def _enabled():
    return bool(getattr(settings, "ASSIGNMENT_PDF_CACHE_ENABLED", True))


def _ttl():
    # Longer than the project's general-purpose CACHE_TTL (5 minutes, for
    # cheap-to-rebuild list/dashboard payloads): a PDF costs a full
    # browser render to rebuild, and the timestamped key already
    # guarantees an edited assignment is never served from here, so a
    # long TTL trades only memory for a much better hit rate.
    return getattr(settings, "ASSIGNMENT_PDF_CACHE_TTL_SECONDS", 60 * 60 * 24)


def _max_bytes():
    # A typical assignment PDF measured ~43KB, but one with many embedded
    # images can run to megabytes, and nothing upstream bounds it. Without
    # a cap, a handful of pathological assignments could hold tens/hundreds
    # of MB of Redis for a full TTL and evict everything else. Skipping the
    # write for oversized renders costs those few downloads their cache hit
    # and protects every other entry. 0 disables the cap.
    return getattr(settings, "ASSIGNMENT_PDF_CACHE_MAX_BYTES", 5 * 1024 * 1024)


def build_cache_key(assignment, view_type: str) -> str:
    # updated_at can be None for an in-memory instance that was never
    # saved; such an assignment has no stable identity to cache against,
    # so fall back to a literal that simply never matches a stored entry.
    stamp = (
        assignment.updated_at.isoformat()
        if getattr(assignment, "updated_at", None)
        else "unsaved"
    )
    return f"{CACHE_KEY_PREFIX}:{CACHE_VERSION}:{assignment.id}:{view_type}:{stamp}"


def get_cached_pdf(assignment, view_type: str):
    """Returns cached PDF bytes, or None on a miss/disabled/error."""
    if not _enabled():
        return None
    try:
        return cache.get(build_cache_key(assignment, view_type))
    except Exception:
        logger.exception("[PDF] cache read failed - rendering this assignment fresh.")
        return None


def store_pdf(assignment, view_type: str, pdf_bytes: bytes) -> None:
    """Writes one rendered PDF to the cache. Never raises."""
    if not _enabled():
        return

    max_bytes = _max_bytes()
    if max_bytes and len(pdf_bytes) > max_bytes:
        logger.info(
            "[PDF] not caching assignment %s (%s view): %s bytes exceeds the "
            "%s-byte cap; it will be re-rendered on each download.",
            assignment.id,
            view_type,
            len(pdf_bytes),
            max_bytes,
        )
        return

    try:
        cache.set(build_cache_key(assignment, view_type), pdf_bytes, timeout=_ttl())
    except Exception:
        logger.exception(
            "[PDF] cache write failed - continuing without caching this render."
        )
