"""Epic C1, Gate 3 (plan 11 §5.3): real threads on real Postgres,
released together by a barrier, repeated 100 times.

1. Ten teachers' requests racing to create the same tag, typed ten ways:
   exactly one row, and every caller gets its id (the IntegrityError
   re-read in get_or_create_tag).
2. A delete racing an attach on one tag: never a link to a deleted tag.
   Either the delete wins (the attach answers "not found") or the attach
   wins (the delete's count check refuses it). Nothing else may happen.
"""

import importlib

from django.http import Http404
from django.test import TransactionTestCase

from AutoGrader.reason_codes import CodedError
from AutoGrader.testing.concurrency import run_concurrently
from tags._c1_test_fixtures import World
from tags.models import AssignmentTag, Tag, TagType

# Not built yet when the expectations are committed (red first).
services = importlib.import_module("tags.services")

ROUNDS = 100
SPELLINGS = [
    " Fractions",
    "fractions",
    "FRACTIONS",
    "Fractions ",
    "fRaCtIoNs",
    "Fractions ",
    "\tfractions",
    "Ｆractions",
    "Frac​tions",
    "  fractions  ",
]


class CreateRaceTest(TransactionTestCase):
    def test_one_row_and_one_id_for_every_caller(self):
        w = World().build()
        for round_number in range(ROUNDS):
            Tag.objects.all().delete()

            def worker(i):
                tag, _ = services.get_or_create_tag(
                    w.teacher, TagType.UNIT, SPELLINGS[i]
                )
                return tag.pk

            results, errors = run_concurrently(worker, len(SPELLINGS), test=self)
            self.assertEqual(errors, [], f"round {round_number}")
            self.assertEqual(Tag.objects.count(), 1, f"round {round_number}")
            self.assertEqual(
                set(results), {Tag.objects.get().pk}, f"round {round_number}"
            )


class DeleteAttachRaceTest(TransactionTestCase):
    def test_never_a_link_to_a_deleted_tag(self):
        w = World().build()
        outcomes = {"delete_won": 0, "attach_won": 0}
        for round_number in range(ROUNDS):
            AssignmentTag.objects.all().delete()
            Tag.objects.all().delete()
            tag, _ = services.get_or_create_tag(w.teacher, TagType.UNIT, "Cells")

            def worker(i, tag=tag):
                if i == 0:
                    return ("delete", services.delete_tag(tag, expected_count=0))
                services.set_assignment_tags(w.a1, [tag.pk], teacher=w.teacher)
                return ("attach", None)

            results, errors = run_concurrently(worker, 2, test=self)
            unexpected = [e for e in errors if not isinstance(e, (Http404, CodedError))]
            self.assertEqual(unexpected, [], f"round {round_number}")
            for error in errors:
                if isinstance(error, CodedError):
                    self.assertEqual(error.reason_code, "TAG_CHANGED_SINCE_CHECK")
            self.assertEqual(
                len(errors), 1, f"round {round_number}: {results} {errors}"
            )
            tag_exists = Tag.objects.filter(pk=tag.pk).exists()
            links = AssignmentTag.objects.filter(tag_id=tag.pk).count()
            if tag_exists:
                outcomes["attach_won"] += 1
                self.assertEqual(links, 1, f"round {round_number}")
            else:
                outcomes["delete_won"] += 1
                self.assertEqual(links, 0, f"round {round_number}")
                self.assertFalse(AssignmentTag.objects.exists())
        print(f"\nC1_RACE delete_attach {outcomes}")
