"""S6b author mutants.

Run from the S6b worktree at its gated commit: `python mutate.py [NAME ...]`,
or `python mutate.py check` to confirm every anchor matches exactly once.
"""

import hashlib
import os
import re
import subprocess
import sys

NEW = "assignments.tests_file_reason_codes"
SVC = "assignments/services.py"
MUTANTS = {
    # An undeclared type falls through and is silently accepted with no file content.
    "M1_type_check_removed": (
        SVC,
        "        if uploaded_file.content_type not in (*cls.IMAGE_FORMATS, cls.PDF_FORMAT):\n",
        "        if False:\n",
        [NEW],
    ),
    # A zero-byte file is left to the parsers (reads as unreadable).
    "M2_empty_check_removed": (
        SVC,
        "        if not uploaded_file.size:\n",
        "        if False:\n",
        [NEW],
    ),
    # A zero-page PDF is called unreadable instead of empty.
    "M3_zero_pages_unreadable": (
        SVC,
        (
            "            except PDFEmptyError as exc:\n"
            "                raise SubmissionEmptyError("
        ),
        (
            "            except PDFEmptyError as exc:\n"
            "                raise FileUnreadableError("
        ),
        [NEW],
    ),
    # The pixel cap answers as an unreadable file.
    "M4_pixels_unreadable": (
        SVC,
        '            raise _too_many_pixels(file_name, f"{width}x{height} px")\n',
        '            raise FileUnreadableError(params={"file_name": file_name})\n',
        [NEW, "assignments.tests_security"],
    ),
    # Too many pages answers as unreadable.
    "M5_pages_unreadable": (
        SVC,
        (
            "            except PDFTooManyPagesError as exc:\n"
            "                raise FileTooLargeError(\n"
        ),
        (
            "            except PDFTooManyPagesError as exc:\n"
            '                raise FileUnreadableError(params={"file_name": file_name}) from exc\n'
            "                raise FileTooLargeError(\n"
        ),
        [NEW],
    ),
    # The background task re-wraps the coded refusal again (the code is lost).
    "M6_task_rewraps": (
        "assignments/tasks.py",
        (
            "        except InvalidUploadFileError:\n"
            "            # A coded file refusal (FR-A-06 S6b) goes on as itself, so the\n"
            "            # item keeps its reason code.\n"
            "            raise\n"
        ),
        "",
        [NEW],
    ),
    # An empty ASSIGNMENT file keeps SUBMISSION_EMPTY's student-answers text.
    "M7_assignment_empty_not_mapped": (
        "assignments/file_uploads.py",
        "        except SubmissionEmptyError as exc:\n",
        "        except ZeroDivisionError as exc:\n",
        [NEW],
    ),
    # The byte cap is bound at definition time again (a patched limit is ignored).
    "M8_cap_bound_at_def": (
        "AutoGrader/uploads.py",
        (
            "    if max_size_bytes is None:\n"
            "        max_size_bytes = MAX_UPLOAD_SIZE_BYTES\n"
        ),
        (
            "    if max_size_bytes is None:\n"
            "        max_size_bytes = 50 * 1024 * 1024\n"
        ),
        [NEW],
    ),
    # The parser-failure message conflates #3 and #4 again.
    "M9_infra_text_conflated": (
        "AutoGrader/error_messages.py",
        '            "or incomplete.",\n',
        '            "or in an unsupported format.",\n',
        [NEW],
    ),
    # An unsupported type is named by its declared MIME type only.
    "M10_type_named_by_mime": (
        SVC,
        "    if dot and stem and extension and len(extension) <= 10:\n",
        "    if False:\n",
        [NEW],
    ),
    # An image too large even compressed answers as unreadable.
    "M11_compression_unreadable": (
        SVC,
        (
            "        except ImageCompressionError as exc:\n"
            "            raise _too_large_after_compression(file_name, exc) from exc\n"
            "        except DecompressionBombError"
        ),
        (
            "        except ImageCompressionError as exc:\n"
            '            raise FileUnreadableError(params={"file_name": file_name}) from exc\n'
            "        except DecompressionBombError"
        ),
        [NEW],
    ),
    # The PDF path's compression failure answers as unreadable.
    "M12_pdf_compression_unreadable": (
        SVC,
        (
            "            except ImageCompressionError as exc:\n"
            "                raise _too_large_after_compression(file_name, exc) from exc\n"
            "            except (PDFUnreadableError"
        ),
        "            except (PDFUnreadableError",
        [NEW],
    ),
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


if sys.argv[1:] == ["check"]:
    for n, (p, o, _, _) in MUTANTS.items():
        print(n, open(p).read().count(o))
    sys.exit()

for name in sys.argv[1:] or MUTANTS:
    path, old, new, mods = MUTANTS[name]
    pristine = subprocess.check_output(["git", "show", f"HEAD:{path}"])
    src = open(path).read()
    if src.count(old) != 1:
        print(name, "APPLY_FAILED", src.count(old), flush=True)
        continue
    open(path, "w").write(src.replace(old, new))
    try:
        out = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--scope",
                "-q",
                "-p",
                "MemoryMax=6G",
                "-p",
                "MemorySwapMax=0",
                "nice",
                "-n",
                "10",
                "timeout",
                "-k",
                "60",
                "1800",
                sys.executable,
                "manage.py",
                "test",
                *mods,
                "--settings=settings_worktree",
                "--noinput",
                "--keepdb",
                "-v1",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        o = out.stdout + out.stderr
        failing = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", o, re.M)))
        summary = next(
            (line for line in o.splitlines() if line.startswith(("OK", "FAILED"))),
            f"? rc={out.returncode}",
        )
        print(name, "KILLED" if failing else "SURVIVED", summary, failing, flush=True)
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)
        print(
            "  restore",
            "ok" if sha(open(path, "rb").read()) == sha(pristine) else "MISMATCH",
            flush=True,
        )
