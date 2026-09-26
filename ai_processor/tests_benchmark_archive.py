"""
Coverage for the Tier 3 benchmark run archive.

The properties locked here are mostly about SAFETY rather than features. A
paid benchmark run costs real money and about an hour, so the archive code's
most important promise is that it can never destroy one:

- archive_run() never raises, whatever goes wrong.
- The local safety copy is written BEFORE the upload is attempted, so an
  upload failure loses nothing.
- Replay runs are not archived (they are deterministic — the same bytes every
  night).
- The bundle carries only THIS run's model calls, not the accumulated
  recordings file.
- Tests never touch real Cloudinary.

ON THAT LAST POINT: this repo's tests run with ENVIRONMENT=local, and "local"
maps to real Cloudinary storage (AutoGrader/settings.py). There was no
storage-mocking convention here before this feature, so these tests establish
one: the backend is chosen via BENCHMARK_ARCHIVE_STORAGE and overridden to
Django's in-memory storage, with settings additionally defaulting
BENCHMARK_ARCHIVE_ENABLED to False under `manage.py test` as a backstop.

Run with:
    python manage.py test ai_processor.tests_benchmark_archive
"""

import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_processor.benchmark import archive, history, scoring
from ai_processor.tests_benchmark_history import _synthetic_run

IN_MEMORY = "django.core.files.storage.InMemoryStorage"


def _bundle_inputs(mode="record"):
    run = _synthetic_run(mode=mode)
    run["responses"] = {"promptkey1": {"content": "{}", "total_tokens": 10}}
    report = scoring.score_run(run)
    run_record = history.build_run_record(run, report, run_id="run-1")
    question_records = history.build_question_records(run, "run-1")
    return run, report, run_record, question_records


class StorageSafetyTest(SimpleTestCase):
    def test_archiving_is_disabled_by_default_under_tests(self):
        # The backstop. ENVIRONMENT=local resolves to real Cloudinary, so a
        # test that uploaded would make a live network call with real
        # credentials. settings.py sets this False when running `test`.
        from django.conf import settings

        self.assertFalse(
            settings.BENCHMARK_ARCHIVE_ENABLED,
            "Benchmark archiving must be OFF during tests — see settings.py.",
        )

    def test_configured_backend_is_not_a_network_backend_during_tests(self):
        from django.conf import settings

        self.assertFalse(
            settings.BENCHMARK_ARCHIVE_ENABLED
            and "cloudinary" in settings.BENCHMARK_ARCHIVE_STORAGE.lower(),
            "Tests must not be able to upload to Cloudinary.",
        )


class ShouldArchiveTest(SimpleTestCase):
    @override_settings(BENCHMARK_ARCHIVE_ENABLED=True)
    def test_paid_modes_are_archived(self):
        self.assertTrue(archive.should_archive("live"))
        self.assertTrue(archive.should_archive("record"))

    @override_settings(BENCHMARK_ARCHIVE_ENABLED=True)
    def test_replay_is_not_archived(self):
        # Replay re-reads fixed responses, so archiving the nightly job would
        # upload one identical file 365 times a year.
        self.assertFalse(archive.should_archive("replay"))

    @override_settings(BENCHMARK_ARCHIVE_ENABLED=False)
    def test_kill_switch_disables_everything(self):
        self.assertFalse(archive.should_archive("record"))


class BundleTest(SimpleTestCase):
    def test_bundle_is_self_contained(self):
        run, report, run_record, question_records = _bundle_inputs()
        bundle = archive.build_bundle(run, report, run_record, question_records)

        # Embedding its own history rows is what makes the git files
        # regenerable from archives — and therefore a server-side Celery run,
        # which cannot commit to git, recoverable.
        self.assertEqual(bundle["run_record"]["run_id"], "run-1")
        self.assertEqual(len(bundle["question_records"]), 2)
        self.assertIn("report", bundle)
        self.assertIn("responses", bundle)
        self.assertEqual(bundle["schema_version"], archive.SCHEMA_VERSION)

    def test_student_answers_are_included_for_later_forensics(self):
        # The LaTeX evidence bug was found by diffing the model's quotes
        # against the student's actual answer text. Ground truth has been
        # corrected mid-flight before, so an archive must not depend on the
        # current dataset still matching.
        run, report, run_record, question_records = _bundle_inputs()
        bundle = archive.build_bundle(run, report, run_record, question_records)
        specs = bundle["results"][0]["specs"]
        self.assertTrue(specs)
        self.assertIn("answer_html", specs[0])

    def test_bundle_is_json_serialisable_despite_dataclasses(self):
        run, report, run_record, question_records = _bundle_inputs()
        bundle = archive.build_bundle(run, report, run_record, question_records)
        json.dumps(bundle, default=str)

    def test_compress_round_trips_and_is_reproducible(self):
        run, report, run_record, question_records = _bundle_inputs()
        bundle = archive.build_bundle(run, report, run_record, question_records)

        first = archive.compress(bundle)
        second = archive.compress(bundle)
        # mtime=0, so identical input gives identical bytes — an unchanged
        # archive should never look like a new one.
        self.assertEqual(first, second)

        restored = json.loads(gzip.decompress(first).decode("utf-8"))
        self.assertEqual(restored["run_id"], "run-1")


class ArchiveRunTest(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_successful_archive_returns_url_and_writes_local_copy(self):
        run, report, record, questions = _bundle_inputs()
        url, error, local_path = archive.archive_run(
            run, report, record, questions, directory=self.dir
        )
        self.assertIsNone(error)
        self.assertTrue(url)
        self.assertTrue(local_path.exists())
        self.assertEqual(archive.load_bundle(local_path)["run_id"], "run-1")

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_upload_failure_keeps_the_local_copy_and_never_raises(self):
        # THE CENTRAL SAFETY PROMISE. An hour of paid grading must survive
        # Cloudinary being unreachable.
        with patch.object(
            archive, "upload", side_effect=OSError("Cloudinary unreachable")
        ):
            run, report, record, questions = _bundle_inputs()
            url, error, local_path = archive.archive_run(
                run, report, record, questions, directory=self.dir
            )

        self.assertIsNone(url)
        self.assertIn("Cloudinary unreachable", error)
        self.assertTrue(
            local_path.exists(), "the local safety copy must survive an upload failure"
        )
        self.assertEqual(archive.load_bundle(local_path)["run_id"], "run-1")

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_local_write_failure_still_attempts_upload(self):
        with patch.object(archive, "save_local", side_effect=OSError("disk full")):
            run, report, record, questions = _bundle_inputs()
            url, error, local_path = archive.archive_run(
                run, report, record, questions, directory=self.dir
            )
        self.assertTrue(url)
        self.assertIsNone(error)
        self.assertIsNone(local_path)

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_bundle_build_failure_is_reported_not_raised(self):
        with patch.object(archive, "build_bundle", side_effect=ValueError("bad")):
            run, report, record, questions = _bundle_inputs()
            url, error, local_path = archive.archive_run(
                run, report, record, questions, directory=self.dir
            )
        self.assertIsNone(url)
        self.assertIn("ValueError", error)

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_replay_run_is_skipped_entirely(self):
        run, report, record, questions = _bundle_inputs(mode="replay")
        url, error, local_path = archive.archive_run(
            run, report, record, questions, directory=self.dir
        )
        self.assertEqual((url, error, local_path), (None, None, None))
        self.assertEqual(list(self.dir.iterdir()), [])

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=True, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_force_archives_a_replay_anyway(self):
        run, report, record, questions = _bundle_inputs(mode="replay")
        url, error, _ = archive.archive_run(
            run, report, record, questions, directory=self.dir, force=True
        )
        self.assertTrue(url)
        self.assertIsNone(error)

    @override_settings(
        BENCHMARK_ARCHIVE_ENABLED=False, BENCHMARK_ARCHIVE_STORAGE=IN_MEMORY
    )
    def test_disabled_archiving_writes_nothing(self):
        run, report, record, questions = _bundle_inputs()
        url, error, local_path = archive.archive_run(
            run, report, record, questions, directory=self.dir
        )
        self.assertEqual((url, error, local_path), (None, None, None))
