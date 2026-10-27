# H-258 notes (Hardening Engineer, 4d)

- **The row** (batch 29 row C): pip-audit in CI, on every push and pull request to main, beta and dev, and weekly.
  Its version is pinned, the findings were reported before any fix, and the accepted advisories sit in an ignore
  file with a reason and a review date.
- **Base:** beta `31a86456`. Branch `task/h258-pip-audit-ci`:
  - tests `91d1c8c4`;
  - code `28133025`;
  - the H-263 reasons in the accepted list `4ee24070`;
  - docs and gate pack (this folder).

## The audit before any fix (2026-10-27)

- **How it was run:** pip-audit 2.9.0, in a scratch venv outside the repo:
  `pip-audit -r requirements.txt --strict --no-deps --disable-pip`.
  - All 223 lines of requirements.txt are pinned with `==`, so `--no-deps` audits exactly what is listed without
    installing it.
  - The full JSON is `audit_before_31a86456.json.gz`.
- **Result:** "Found 250 known vulnerabilities in 32 packages". "Needs" is the lowest version that clears every
  advisory on the package, staying in the current major where a fix exists there (`findings_table.txt`).
- **Patch-level (upgraded in this row, SM ruling (a)):**

  | Package | Was | Now | Advisories |
  |---|---|---|---|
  | Django | 5.2.6 | 5.2.17 | 32 |
  | tornado | 6.5.4 | 6.5.9 | 11 |
  | Authlib | 1.6.3 | 1.6.12 | 10 |
  | pyasn1 | 0.6.3 | 0.6.4 | 3 |
  | brotli | 1.2.0 | 1.2.1 | 2 |

  - Who uses them: only Django is imported by our code. Authlib comes in through `safety` (a dev tool), tornado
    through `flower`, and pyasn1 through `pyasn1_modules` (google-auth). Nothing requires brotli.
- **Minor and major (accepted for now, SM ruling (b)):** 27 packages and 192 advisories, all in
  `.pip-audit-ignore.toml`.
  - Review date 2026-11-30.
  - **The owner's rulings (2026-10-27):** gunicorn 22.0.0, pillow 13.0.0, pyjwt 2.15.0, lxml 6.1.0 and urllib3
    2.8.0 are approved for upgrade in **H-263 (batch 30)**. A second ruling the same day added djangorestframework
    3.18.2, which is reachable unauthenticated. Their entries say so. The rest stay on the list to
    the review date.
- **No fixed version yet:** four nltk advisories (PYSEC-2026-97, -597, -3955, -4318) and one pyjwt advisory
  (PYSEC-2025-183, disputed by the supplier).

## The eight packages (for the owner's ruling; SM, 2026-10-27)

- **How the table was made:** each worst advisory was chosen by reading the advisory text that pip-audit returned
  (`eight_advisories.txt`). "Reached" comes from a search of our code, not a deep audit.

| Package | Worst advisory | Its effect (one sentence) | Does our code reach it? |
|---|---|---|---|
| gunicorn 20.0.4 | PYSEC-2026-1434 (CVE-2024-1135) | Conflicting Transfer-Encoding headers allow request smuggling that can bypass restrictions and reach restricted endpoints. | **Yes**: gunicorn serves every request. Whether Railway's edge passes such headers through was not checked. |
| pillow 12.0.0 | PYSEC-2026-2252 (CVE-2026-42311) | A malicious PSD file can corrupt memory, possibly to code execution. | **Yes, likely**: `assignments/services.py:546` opens uploaded bytes with `Image.open` and no `formats=` limit. Whether an earlier type check refuses a PSD was not checked. |
| pyjwt 2.10.1 | PYSEC-2026-179 (CVE-2026-48526) | When both asymmetric and HMAC algorithms are allowed, the issuer's public key can be used as the HMAC secret to forge tokens. | **No**: `SIMPLE_JWT` uses HS256 only, and our code has no `PyJWKClient`. pyjwt itself decodes the token on every authenticated request. |
| cryptography 45.0.7 | PYSEC-2026-36 (CVE-2026-39892) | A non-contiguous buffer passed to APIs such as `Hash.update()` can overflow. | **No** for that path. We use it through Fernet field encryption and google-auth, with plain bytes. The bundled-OpenSSL advisory (GHSA-537c-gmf6-5ccf) was not checked. |
| lxml 6.0.2 | PYSEC-2026-87 (CVE-2026-41066) | The default parsers resolve entities, so untrusted XML can read local files. | **Not checked**: `lxml.html` parses user HTML in `assignments/prosemirror_converter.py` and `assignments/services.py`. Whether the HTML parser path reads entities was not checked. |
| urllib3 2.5.0 | PYSEC-2026-1998 (CVE-2025-66418) | A malicious server can send an unbounded chain of content encodings, causing high CPU and memory use. | **No in practice**: requests (Google, MailerLite, Stripe) uses it only toward those known hosts. The advisory needs a hostile server. The redirect-header advisory (PYSEC-2026-141) needs `ProxyManager`, which we do not use. |
| nltk 3.9.1 | PYSEC-2026-96 (CVE-2025-14009) | The downloader extracts zip packages without path checks, so a malicious package can execute code. | **No**: our code never imports nltk. It comes in through `safety`, a dev tool. **None of nltk's 42 advisories reaches production code**, so the recommendation for nltk is "ignore until H-262 removes it". |
| djangorestframework 3.16.1 | GHSA-3547-9m27-7rxg (CVE-2026-114136) | An unauthenticated request can name a compression codec in its Content-Type charset and make a 108-byte body allocate about 97 MiB, past `DATA_UPLOAD_MAX_MEMORY_SIZE`. | **Yes**: any route that reads `request.data`, the anonymous sign-in routes included. |

## What changes

- **`.github/workflows/audit.yml`** runs pip-audit 2.9.0 (pinned) through `scripts/pip_audit_gate.py`.
  - It runs on every push and pull request to main, beta and dev, and on a weekly schedule (Mondays 06:17 UTC).
  - GitHub runs a schedule on the default branch only.
- **`scripts/pip_audit_gate.py`** runs
  `python -m pip_audit -r requirements.txt --strict --no-deps --disable-pip --progress-spinner off` with one
  `--ignore-vuln` per accepted id. It refuses, before pip-audit runs:
  - an entry with no package, no ids, no reason, or no review date (a TOML date, not a string);
  - an id listed twice;
  - an entry whose review date has passed (the day itself still counts);
  - a pip-audit version other than the pinned one.
- **`.pip-audit-ignore.toml`** holds 27 entries, one advisory id per line (SM ruling (c)), so a new advisory on a
  listed package is not covered.
- **`requirements.txt`:** the five patch-level pins.
- **Stale comments:**
  - `AutoGrader/settings.py` (the Beat comment on the nightly replay) and `docs/backend/ai-quality-harness.md`
    (two places) no longer say "there is no CI in this repo".
  - **The rest of the "no CI" notes** (SM ruling, 2026-10-27; commit `51043d7a`): `ai_processor/tasks.py`'s module
    docstring and `docs/backend/BACKEND_REFERENCE.md` (1961, D13, observation 1) now name what the workflows run
    (the tests, pre-commit, migration safety, the dependency audit) and what they do not (the grading benchmarks).
  - **Three docs that quoted the old docstring** (`ai-quality-harness.md`, `operations.md`, `README.md`) quote the
    new one, so none quotes text that no longer exists.

## The shared virtualenv was not touched

- **Where the near suites ran:** the shared `AutoGrader_env` keeps the old pins (Django 5.2.6). Every Django run
  of this row used a private copy, `~/Documents/Virtualenvs/AutoGrader_env_h258`.
  - It was copied from the shared venv, then exactly the five pins were installed.
  - `pip freeze` differs from the shared venv on those five lines only.
- **The batch 29 full run** installs the pins into the shared venv from the bundle worktree first; that is the
  Release Builder's record.
- **The private venv is removed after this row's gate,** so it does not outlive the row (SM, 2026-10-27).

## Tests (`AutoGrader.tests_pip_audit_gate`, 12 tests)

- **IgnoreFileTests** (8):
  - an entry gives its ids, and an empty file accepts nothing;
  - an entry on its review date still counts, and one past it is refused;
  - refused: no reason (missing or blank), no review date (missing or a string), no ids or no package, an id
    listed twice.
- **CommandTests** (2):
  - the command is `-m pip_audit -r requirements.txt` with --strict, --no-deps and --disable-pip, and the ids in
    order;
  - a wrong pip-audit version is refused.
- **CheckedInFilesTests** (2):
  - the checked-in ignore file is well formed. It is read with a day before any review date: a passed date fails
    the CI job, not the test suite;
  - the workflow's triggers (push and pull request to main, beta and dev; a schedule), its pinned install, and its
    runner step.

## Expected red against the base (written before any run)

- The three new files are absent, so every test errors on the missing file. That is 12 ERROR blocks, each holding
  `FileNotFoundError: [Errno 2] No such file or directory`, and "Ran 12 tests".

## Mutants (12; expected sets and fragments written before any mutant ran)

| Id | Break | Expected to fail | Fragment |
|---|---|---|---|
| R1 | a passed review date accepted | past-date test | `AssertionError: IgnoreFileError not raised` |
| R2 | an entry refused ON its review date (`<=`) | on-date test | `IgnoreFileError: gunicorn: the review date 2026-11-30 has passed` |
| R3 | an entry without a reason accepted | no-reason test (2 blocks) | `AssertionError: IgnoreFileError not raised` |
| R4 | an id listed twice accepted | twice test | `AssertionError: IgnoreFileError not raised` |
| R5 | the review date's type not checked | no-date test (2 blocks) | `TypeError: '<' not supported between instances of` |
| R6 | a wrong pip-audit version accepted | version test | `AssertionError: IgnoreFileError not raised` |
| R7 | `--strict` dropped | command test | `'--strict' not found in [` |
| R8 | the accepted ids not passed | command test | `[] != ['B-2', 'A-1']` |
| R9 | the weekly schedule dropped | workflow test | `KeyError: 'schedule'` |
| R10 | pip-audit unpinned in the workflow | workflow test | `'pip install "pip-audit==2.9.0"' not found in` |
| R11 | pushes to beta not audited | workflow test | `['main', 'dev'] != ['main', 'beta', 'dev']` |
| R12 | a checked-in entry without its reason (anyio) | checked-in file test | `IgnoreFileError: anyio: no reason` |

## The audit itself and the seam

- **The audit at the tip, through the runner:** "No known vulnerabilities found, 192 ignored", exit 0.
- **Planted reds,** run in a copy outside the tree:
  - **(A)** the base pins with the accepted list: "Found 58 known vulnerabilities, ignored 192 in 5 packages",
    exit 1. These are the five patch-level packages.
  - **(B)** the tip with PYSEC-2026-1434 (gunicorn) removed from the list: "Found 1 known vulnerability, ignored
    191 in 1 package", exit 1.
- **The seam:**
  - the changed files outside `docs/` are exactly the seven;
  - `AutoGrader/settings.py`'s syntax tree equals the base's (a comment only);
  - `ai_processor/tasks.py`'s syntax tree equals the base's apart from the module docstring, which no longer says
    there is no CI;
  - `requirements.txt` has the same number of lines and differs by exactly the five pins.

## Gate

- **Script:** `run_h258_gate.sh.txt`. Every Django run uses the private venv.
- **Order:**
  1. the red run;
  2. makemigrations under Django 5.2.17;
  3. the changed files and the seam;
  4. the audit and the two planted reds;
  5. the full suite with `--parallel 4` (the check for the Django patch upgrade);
  6. the 12 mutants.
- **Development checks before the pack** (not the gate), in the private venv:
  - makemigrations "No changes detected";
  - `manage.py check` with no issues;
  - flower, google-auth's id_token, `authlib.jose` and a brotli round trip all import and work.

## The first gate run on 90a08225: stopped by me for the owner's second ruling

- **Steps 0 to 1c were as written:**
  - red run: 12 blocks, each holding FileNotFoundError;
  - makemigrations: no changes;
  - seam OK;
  - the audit at the tip: "No known vulnerabilities found, 192 ignored";
  - planted A: 58 found; planted B: 1 found.
- **Why it stopped:** the owner's ruling to add djangorestframework to H-263 arrived while the full suite (step 1)
  was 40 s in. The Senior Manager asked for the gate on the tip that carries it, so I stopped my own suite scope
  (`gap-h258-895210-suite.scope`); the script recorded exit 124 and "STOP: not green".
- No test had failed. The output is kept in `stop_90a08225/` (gzipped).
- The gate runs again from step 0 on the tip with the ruling.
