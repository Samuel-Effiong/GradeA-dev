"""User list plant harness: copy, mutate, sha check, run, restore, sha check.
Brief 5 (AI prompt leads, beta): plants L1-L5.

The saved copy is deleted once the restore sha matches (the sha lines in
the output are the proof); it is kept only on a mismatch.

Usage (from the worktree root): python plant_uls.py <PLANT_ID> <log_path>
"""

import hashlib
import os
import shutil
import subprocess
import sys

PLANTS = {
    "L1-slice": (
        "billing/views.py",
        [
            (
                "            intent_leads = ConversionLeadSerializer(\n"
                "                lead_rows(top_leads(signal_rules.request_now(request), 10)),\n"
                "                many=True,\n"
                "            ).data\n",
                "            intent_leads = self.intent_signals(request, *args, **kwargs).data[:10]\n",
            ),
        ],
    ),
    "L2-order": (
        "billing/views.py",
        [
            (
                "    leads = signal_rules.annotate_signals(get_teacher_beta_profiles(), now).order_by(\n"
                '        "-conversion_probability", "-total_credits_used"\n'
                "    )\n",
                "    leads = signal_rules.annotate_signals(get_teacher_beta_profiles(), now).order_by(\n"
                '        "-total_credits_used"\n'
                "    )\n",
            ),
        ],
    ),
    "L3-email": (
        "billing/views.py",
        [
            (
                "                lead_rows(top_leads(signal_rules.request_now(request), 10)),\n",
                '                [{k: v for k, v in row.items() if k != "email"} for r'
                "ow in lead_rows(top_leads(signal_rules.request_now(request), 10))],\n",
            ),
        ],
    ),
    "L3b-email-after": (
        "billing/views.py",
        [
            (
                "                many=True,\n"
                "            ).data\n"
                "        except DatabaseError as exc:\n",
                "                many=True,\n"
                "            ).data\n"
                '            intent_leads = [{k: v for k, v in row.items() if k != "email"} for row in intent_leads]\n'
                "        except DatabaseError as exc:\n",
            ),
        ],
    ),
    "L4-bare-except": (
        "billing/views.py",
        [
            (
                "        except DatabaseError as exc:\n",
                "        except Exception as exc:\n",
            ),
        ],
    ),
    "L5-params": (
        "billing/views.py",
        [
            (
                "lead_rows(top_leads(signal_rules.request_now(request), 10)),",
                "lead_rows(top_leads(signal_rules.request_now(request"
                '), int(request.query_params.get("page_size", 10)))),',
            ),
        ],
    ),
}

LABELS = [
    "billing.tests.test_custom_ai_prompt_leads",
    "billing.tests.test_custom_ai_prompt_beta",
    "billing.tests.test_intent_signals",
    "billing.tests.test_intent_signals_pins",
]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    plant, log = sys.argv[1], sys.argv[2]
    path, edits = PLANTS[plant]
    saved = f"{log}.{path.replace('/', '_')}.orig"
    shutil.copyfile(path, saved)
    original = sha(path)
    committed = subprocess.run(
        ["git", "show", f"HEAD:{path}"], capture_output=True, check=True
    ).stdout
    assert hashlib.sha256(committed).hexdigest() == original, "file differs from HEAD"
    text = open(path).read()
    for old, new in edits:
        assert text.count(old) == 1, (plant, old[:60])
        text = text.replace(old, new)
    open(path, "w").write(text)
    planted = sha(path)
    print(f"{plant} {path} original {original}")
    print(f"{plant} {path} planted  {planted}")
    assert planted != original
    try:
        with open(log, "w") as out:
            subprocess.run(
                [
                    "flock",
                    "/home/bond-servant-in-training/.machine-fullsuite.lock",
                    sys.executable,
                    "manage.py",
                    "test",
                    *LABELS,
                    "--settings=settings_worktree",
                    "--keepdb",
                    "--noinput",
                    "-v",
                    "2",
                ],
                stdout=out,
                stderr=subprocess.STDOUT,
            )
    finally:
        shutil.copyfile(saved, path)
        restored = sha(path)
        print(
            f"{plant} {path} restored {restored} {'OK' if restored == original else 'MISMATCH'}"
        )
        assert restored == original
        os.remove(saved)


if __name__ == "__main__":
    main()
