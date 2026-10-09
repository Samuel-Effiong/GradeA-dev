"""
Tier 3 of the benchmark run history: the complete raw archive.

WHY THIS EXISTS

Tiers 1 and 2 (history.py) store a fixed set of numbers, so they can answer
questions we have already thought of. This tier keeps the raw material —
every model response, every full grading, every student answer — so it can
answer questions we have NOT thought of yet.

That is not hypothetical. The LaTeX evidence bug (FINDINGS.md, Round 4) was
found by re-reading the model's actual quotes and diffing them against what
the student wrote. That investigation was only possible because the run in
question happened to be the most recent one; for any earlier run the data was
already gone. It also means a metric invented tomorrow can be computed for
every archived run, instead of starting from zero.

WHERE IT GOES, AND WHY NOT GIT

About 1 MB per run compressed. The repo has a 500KB large-file guard, and
gzipped blobs do not delta-compress, so every run would add its full weight to
git history forever. Cloudinary is already a hard requirement of this project
(settings reads CLOUDINARY_* with no defaults, so the app cannot boot without
them), which means this adds no new vendor, credentials or setup.

Only paid runs (live/record) are archived. A replay re-reads fixed recorded
responses and produces the same bytes every time, so archiving the nightly job
would upload one identical file 365 times a year.

SAFETY

A paid run costs real money and about an hour. Losing one to a bookkeeping bug
would be far worse than having no bookkeeping, so:

  - The bundle is written to LOCAL DISK FIRST, then uploaded. If the upload
    fails the data still exists and can be pushed later; nothing is lost.
  - archive_run() never raises. It returns (url, error, local_path) and the
    caller records whichever happened.
"""

import gzip
import io
import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

BENCHMARK_DIR = Path(__file__).resolve().parent
# Gitignored: this is the local safety copy, not a tracked artifact.
LOCAL_ARCHIVE_DIR = BENCHMARK_DIR / "archives"

# Bumped when the bundle's shape changes, so a future reader can tell which
# layout it is looking at rather than guessing from the keys present.
SCHEMA_VERSION = 1


def _plain(value):
    """Make dataclasses and other odd objects JSON-safe."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _serialise_result(result):
    """
    One submission, kept self-contained.

    The student's answers are included even though they also live in the
    dataset in git: an archive should stay readable after the dataset moves
    on, and ground truth HAS been corrected mid-flight before (maths/strong
    Q4). The BenchmarkAssignment object itself is not included — it is large,
    static, and identified by the run's dataset_fingerprint.
    """
    return {
        "assignment_key": result.get("assignment_key"),
        "student_key": result.get("student_key"),
        "error": result.get("error"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "tokens": result.get("tokens"),
        "specs": [_plain(spec) for spec in result.get("specs") or []],
        "grading": result.get("grading"),
    }


def build_bundle(run, report, run_record, question_records):
    """
    The complete record of one run, self-contained by design.

    It embeds its own Tier 1 and Tier 2 rows, which is what makes the git
    history files regenerable from the archives — and therefore what makes a
    run executed on the server, where Celery cannot commit to git, recoverable
    afterwards.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_record.get("run_id"),
        "run_record": run_record,
        "question_records": list(question_records),
        "report": report,
        "mode": run.get("mode"),
        "model_calls": run.get("model_calls"),
        "total_tokens": run.get("total_tokens"),
        # Exactly this run's model calls. runner filters by the keys the run
        # actually used, because in record mode the recordings file also
        # holds responses merged in from earlier runs and retired prompts.
        "responses": run.get("responses") or {},
        "results": [_serialise_result(result) for result in run.get("results") or []],
    }


def compress(bundle):
    """Gzip the bundle. mtime=0 so identical input gives identical bytes."""
    payload = json.dumps(bundle, sort_keys=True, default=str).encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(payload)
    return buffer.getvalue()


# Defaults duplicated from settings.py deliberately: every settings lookup
# here uses getattr with a fallback so this module keeps working — degrading
# to "archiving off" rather than raising — in an environment whose settings
# file predates these keys.
DEFAULT_STORAGE = "cloudinary_storage.storage.RawMediaCloudinaryStorage"
DEFAULT_PREFIX = "benchmark_archives"


def archive_name(run_id):
    prefix = getattr(settings, "BENCHMARK_ARCHIVE_PREFIX", DEFAULT_PREFIX)
    return f"{prefix}/{run_id}.json.gz"


def save_local(run_id, blob, directory=None):
    """Write the safety copy. Done BEFORE any upload is attempted."""
    directory = Path(directory or LOCAL_ARCHIVE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json.gz"
    path.write_bytes(blob)
    return path


def get_storage():
    """
    Resolve the archive storage backend from settings.

    Indirection through a setting is what lets tests swap in an in-memory
    backend. It matters here more than usual: ENVIRONMENT is "local" in .env
    and "local" maps to real Cloudinary, so a test that saved a file would
    otherwise make a live network call with real credentials.
    """
    return import_string(
        getattr(settings, "BENCHMARK_ARCHIVE_STORAGE", DEFAULT_STORAGE)
    )()


def upload(run_id, blob):
    """Upload and return the URL. Raises on failure — archive_run catches."""
    storage = get_storage()
    stored_name = storage.save(archive_name(run_id), ContentFile(blob))
    return storage.url(stored_name)


def should_archive(mode):
    """
    Paid runs only, and only when archiving is switched on.

    Replay is deterministic, so archiving it would store the same bytes over
    and over for no new information.
    """
    if not getattr(settings, "BENCHMARK_ARCHIVE_ENABLED", False):
        return False
    return mode in ("live", "record")


def prepare(run, report, run_record, question_records, directory=None, force=False):
    """
    Phase 1: build the bundle and write the local safety copy. Fast and
    local, so the caller can do this BEFORE anything slow — the expensive
    data is then already on disk if the upload later hangs or fails.

    Returns (blob, local_path, error). All three are None when the run is
    not eligible for archiving. Never raises.
    """
    if not force and not should_archive(run.get("mode")):
        return None, None, None

    run_id = run_record.get("run_id")

    try:
        blob = compress(build_bundle(run, report, run_record, question_records))
    except Exception as exc:  # bundle building must not break a run either
        logger.exception("[Benchmark] Could not build the run archive.")
        return None, None, f"{type(exc).__name__}: {exc}"

    local_path = None
    try:
        local_path = save_local(run_id, blob, directory=directory)
    except Exception as exc:
        # Not fatal on its own — the upload may still succeed.
        logger.warning("[Benchmark] Could not write the local archive copy: %s", exc)

    return blob, local_path, None


def publish(run_id, blob, local_path=None):
    """
    Phase 2: upload a prepared bundle. Slow and fragile, so it goes last.
    Returns (url, error). Never raises.
    """
    try:
        url = upload(run_id, blob)
    except Exception as exc:
        logger.warning(
            "[Benchmark] Archive upload failed for %s (%s: %s). The local copy "
            "at %s is intact and can be uploaded later.",
            run_id,
            type(exc).__name__,
            exc,
            local_path,
        )
        return None, f"{type(exc).__name__}: {exc}"

    logger.info("[Benchmark] Archived run %s to %s", run_id, url)
    return url, None


def archive_run(run, report, run_record, question_records, directory=None, force=False):
    """
    Convenience wrapper: prepare then publish in one call. Returns
    (url, error, local_path) and never raises.

    The command splits these two phases apart so the report can be printed
    between them; this wrapper is for callers that do not care.
    """
    blob, local_path, error = prepare(
        run, report, run_record, question_records, directory=directory, force=force
    )
    if error:
        return None, error, local_path
    if blob is None:
        return None, None, None

    url, error = publish(run_record.get("run_id"), blob, local_path=local_path)
    return url, error, local_path


def load_bundle(path):
    """Read a local archive back — the forensic entry point."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
        return json.load(handle)
