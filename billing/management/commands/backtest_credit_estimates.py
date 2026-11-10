"""Replay the last 90 days of grading batches against the credit estimate
(Epic B1b-4; plan 08 §3.6 QA-B-03, §9 item 6). Read-only.

    manage.py backtest_credit_estimates [--days 90] [--cold-start]

Prints the band coverage (the share of batches whose actual cost stayed
within p90; the target is 90% +/- 5) overall and per shape x batch-size
cell, with actual/p50 quantiles. --cold-start also prints the suggested
cold_start_cost_factor: actual / the rough tier's own figure (SM ruling
2026-11-09, D-f), and, for information only, actual / the real calls'
recorded estimate (the provider gap).
"""

from django.core.management.base import BaseCommand

from billing import estimates


class Command(BaseCommand):
    help = "Backtest the credit estimate over recent batches (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=estimates.HISTORY_DAYS)
        parser.add_argument("--cold-start", action="store_true")

    def handle(self, *args, **options):
        days = options["days"]
        report = estimates.backtest(days=days)
        self.stdout.write(
            f"batches={report['batches']} coverage={report['coverage']} "
            f"ratio_quantiles={report['ratio_quantiles']}"
        )
        for cell in report["cells"]:
            self.stdout.write(
                f"cell bucket={cell['bucket']} has_images={cell['has_images']} "
                f"size_band={cell['size_band']} batches={cell['batches']} "
                f"coverage={cell['coverage']} ratio_quantiles={cell['ratio_quantiles']}"
            )
        if options["cold_start"]:
            self.stdout.write(
                f"suggested_factor={report['suggested_factor']} "
                f"provider_gap_ratio={estimates.provider_gap_ratio(days=days)}"
            )
