"""H-110 mutants: each is applied to assignments/pdf_renderer.py, the two
H-110 test modules run, the killers are recorded, the file is restored.
Every anchor must occur exactly once and every mutant must parse.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and
assignments/__pycache__ is deleted before each mutant and after each
restore. Rule 18: each run's output goes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

RENDERER = "assignments/pdf_renderer.py"
TESTS = [
    "assignments.tests_pdf_renderer_driver_stderr",
    "assignments.tests_pdf_renderer_driver_stderr_reader",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h110-renderer-driver-stderr")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

MUTANTS = {
    "M1_the_hook_is_not_replaced": (
        "            _playwright_transport._get_stderr_fileno = _ours\n",
        "            _ours  # not installed\n",
    ),
    "M2_the_hook_is_not_put_back": (
        "            finally:\n"
        "                _playwright_transport._get_stderr_fileno = original\n",
        "            finally:\n                pass\n",
    ),
    "M3_two_starts_may_overlap": (
        "        with _driver_start_lock:\n            original = getattr(",
        "        if True:\n            original = getattr(",
    ),
    "M4_the_renderer_does_not_offer_its_pipe": (
        "            with self._driver_stderr.handed_to_playwright():\n",
        "            if True:\n",
    ),
    "M5_the_renderer_keeps_its_write_end_after_the_start": (
        "            # The driver has its own copy by now, or never started.\n"
        "            self._driver_stderr.close_write_end()\n",
        "            pass\n",
    ),
    "M6_the_reader_stops_after_its_first_line": (
        "                    else:\n                        self._emit(line)\n",
        "                    else:\n"
        "                        self._emit(line)\n"
        "                        return\n",
    ),
    "M7_a_last_line_with_no_newline_is_dropped": (
        "            if pending and not dropping:\n",
        "            if False:\n",
    ),
    "M8_an_unfinished_line_is_held_without_bound": (
        "                if len(pending) > _DRIVER_STDERR_MAX_PENDING:\n",
        "                if False:\n",
    ),
    "M9_the_rest_of_a_cut_line_is_logged_as_a_line": (
        '                    pending, dropping = b"", True\n',
        '                    pending, dropping = b"", False\n',
    ),
    "M10_a_long_line_is_not_cut": (
        "            if len(text) > DRIVER_STDERR_MAX_LINE:\n",
        "            if False:\n",
    ),
    "M11_control_characters_pass": (
        '            text = "".join(c if c.isprintable() else "?" for c in text)\n',
        "",
    ),
    "M12_a_failing_logger_ends_the_reader": (
        "        except Exception:  # noqa: BLE001 - the reader must keep reading\n"
        "            pass\n",
        "        except Exception:  # noqa: BLE001 - the reader must keep reading\n"
        "            raise\n",
    ),
    "M13_bytes_are_decoded_strictly": (
        '            text = raw.decode("utf-8", "replace").strip()\n',
        '            text = raw.decode("utf-8").strip()\n',
    ),
    "M14_the_read_end_is_never_closed": (
        "        finally:\n"
        "            try:\n"
        "                os.close(self._read_fd)\n"
        "            except OSError:\n"
        "                pass\n",
        "        finally:\n            pass\n",
    ),
    "M15_close_closes_the_write_end_again": (
        "            if self._write_open:\n                self._write_open = False\n",
        "            if True:\n                self._write_open = False\n",
    ),
    "M16_a_line_goes_to_the_log_past_the_record_factory": (
        '            logger.warning("[PDF] Playwright driver stderr: %s", text)\n',
        "            logger.handle(\n"
        "                logging.LogRecord(\n"
        "                    logger.name,\n"
        "                    logging.WARNING,\n"
        "                    __file__,\n"
        "                    0,\n"
        '                    "[PDF] Playwright driver stderr: %s",\n'
        "                    (text,),\n"
        "                    None,\n"
        "                )\n"
        "            )\n",
    ),
    "M17_blank_lines_are_logged": (
        "            if not text:\n                return\n",
        "",
    ),
}


def clear_pycache():
    shutil.rmtree(pathlib.Path(RENDERER).parent / "__pycache__", ignore_errors=True)


def main():
    original = open(RENDERER).read()
    for name, (a, b) in MUTANTS.items():
        assert original.count(a) == 1, f"{name}: anchor found {original.count(a)} times"
        ast.parse(original.replace(a, b, 1))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: anchors unique, all parse")
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    try:
        for name, (a, b) in MUTANTS.items():
            clear_pycache()
            open(RENDERER, "w").write(original.replace(a, b, 1))
            log = LOGS / f"{name}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *TESTS]
                    + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=900,
                )
            text = log.read_text(errors="replace")
            failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
            ran = re.findall(r"^Ran (\d+) tests?", text, re.M)
            results[name] = {
                "killed": p.returncode != 0,
                "ran": int(ran[-1]) if ran else None,
                "failing_tests": failed,
            }
            print(name, results[name], flush=True)
            open(RENDERER, "w").write(original)
            clear_pycache()
    finally:
        open(RENDERER, "w").write(original)
        clear_pycache()
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])


if __name__ == "__main__":
    main()
