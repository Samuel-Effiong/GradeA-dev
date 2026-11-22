"""Upload quote, slice 1: the door's walk as one pure function (plan rev 3,
section 5 step 2; BE-B-05/06).

`plan_parking(spendable, figures)` is the walk `parked_files` makes: files in
upload order with a running balance, a file that fits takes its figure off
what is left, a file that does not is parked and takes nothing, a file with no
figure (None: the door cannot read it) is left to the task. The door is its
caller and keeps its behavior; the quote's confirm and GET are the other.
"""

import importlib
from typing import Any

from django.test import SimpleTestCase


def door() -> Any:
    return importlib.import_module("assignments.upload_door")


class PlanParking(SimpleTestCase):
    def test_everything_fits(self):
        self.assertEqual(door().plan_parking(100, [30, 30, 30]), {})

    def test_running_balance_parks_the_file_that_does_not_fit(self):
        # 100 - 60 = 40; 50 does not fit (parked, takes nothing); 40 does.
        self.assertEqual(door().plan_parking(100, [60, 50, 40]), {1: 40})

    def test_a_parked_file_takes_nothing_so_a_later_small_one_still_fits(self):
        self.assertEqual(door().plan_parking(10, [50, 5, 5]), {0: 10})

    def test_nothing_fits_parks_every_file(self):
        self.assertEqual(door().plan_parking(0, [1, 2, 3]), {0: 0, 1: 0, 2: 0})

    def test_no_figure_is_left_to_the_task(self):
        self.assertEqual(door().plan_parking(10, [None, 20, None]), {1: 10})

    def test_exact_balance_fits(self):
        self.assertEqual(door().plan_parking(30, [10, 10, 10]), {})

    def test_no_files(self):
        self.assertEqual(door().plan_parking(5, []), {})
