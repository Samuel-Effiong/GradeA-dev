# flake8: noqa: E501
"""H-256 seam, by syntax tree (Hardening Engineer, 4d).

    python -I seam_check.py <base dir> <tip dir>

Each of the four production files is parsed at the base and at the tip. The
tip's H-256 changes are taken out of its tree one by one, each found exactly
once and with exactly the value written below; what is left must equal the
base's tree. So nothing else in these files changed, and each change is the
one named (comments are not in the tree; the docs are not production files).

  users/views.py            the import of describe_error_for_log; the constant
                            GOOGLE_TOKEN_EXCHANGE_TIMEOUT = (5, 10); in
                            _google_auth, the post's timeout= keyword and the
                            exchange-failure log call's arguments
  ai_processor/services.py  `import httpx`; the OpenAI client's timeout= and
                            max_retries=2 keywords, in AIProcessor.__init__
  AutoGrader/settings.py    AI_PROVIDER_TIMEOUT_SECONDS = env.float(..., default=300.0)
  billing/imports.py        stripe.max_network_retries = 2
"""

import ast
import pathlib
import sys


class Seam(Exception):
    pass


def once(found, what):
    if len(found) != 1:
        raise Seam(f"{what}: found {len(found)} times, not once")
    return found[0]


def src(node):
    return ast.unparse(node)


def drop_statement(tree, what, text):
    hits = [(body, s) for body in _bodies(tree) for s in body if src(s) == text]
    body, statement = once(hits, what)
    body.remove(statement)


def _bodies(tree):
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            value = getattr(node, field, None)
            if isinstance(value, list):
                yield value


def function(tree, name):
    return once(
        [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        ],
        f"function {name}",
    )


def views(tree):
    drop_statement(
        tree,
        "the describe_error_for_log import",
        "from AutoGrader.safe_logging import describe_error_for_log",
    )
    drop_statement(
        tree, "the exchange timeout constant", "GOOGLE_TOKEN_EXCHANGE_TIMEOUT = (5, 10)"
    )
    google = function(tree, "_google_auth")
    post = once(
        [
            n
            for n in ast.walk(google)
            if isinstance(n, ast.Call) and src(n.func) == "http_requests.post"
        ],
        "the token exchange post",
    )
    timeout = once(
        [k for k in post.keywords if k.arg == "timeout"], "the post's timeout="
    )
    if src(timeout.value) != "GOOGLE_TOKEN_EXCHANGE_TIMEOUT":
        raise Seam(f"the post's timeout is {src(timeout.value)}")
    post.keywords.remove(timeout)
    log = once(
        [
            n
            for n in ast.walk(google)
            if isinstance(n, ast.Call) and src(n.func) == "logger.error"
        ],
        "the exchange-failure log call",
    )
    tip = "logger.error('Google OAuth token exchange failed: %s', describe_error_for_log(e))"
    if src(log) != tip:
        raise Seam(f"the log call is {src(log)}")
    base = (
        ast.parse("logger.error('Google OAuth token exchange failed', exc_info=e)")
        .body[0]
        .value
    )
    log.args, log.keywords = base.args, base.keywords


def services(tree):
    drop_statement(tree, "import httpx", "import httpx")
    init = once(
        [
            f
            for c in ast.walk(tree)
            if isinstance(c, ast.ClassDef) and c.name == "AIProcessor"
            for f in c.body
            if isinstance(f, ast.FunctionDef) and f.name == "__init__"
        ],
        "AIProcessor.__init__",
    )
    client = once(
        [
            n
            for n in ast.walk(init)
            if isinstance(n, ast.Call) and src(n.func) == "OpenAI"
        ],
        "the OpenAI client",
    )
    wanted = {
        "timeout": "httpx.Timeout(settings.AI_PROVIDER_TIMEOUT_SECONDS, connect=10.0)",
        "max_retries": "2",
    }
    for arg, value in wanted.items():
        keyword = once(
            [k for k in client.keywords if k.arg == arg], f"the client's {arg}="
        )
        if src(keyword.value) != value:
            raise Seam(f"the client's {arg} is {src(keyword.value)}")
        client.keywords.remove(keyword)


def settings_py(tree):
    drop_statement(
        tree,
        "the AI_PROVIDER_TIMEOUT_SECONDS setting",
        "AI_PROVIDER_TIMEOUT_SECONDS = env.float('AI_PROVIDER_TIMEOUT_SECONDS', default=300.0)",
    )


def imports(tree):
    drop_statement(tree, "Stripe's retries", "stripe.max_network_retries = 2")


FILES = {
    "users/views.py": views,
    "ai_processor/services.py": services,
    "AutoGrader/settings.py": settings_py,
    "billing/imports.py": imports,
}


def main(base_dir, tip_dir):
    failed = False
    for path, take_out in FILES.items():
        base = ast.parse(pathlib.Path(base_dir, path).read_text())
        tip = ast.parse(pathlib.Path(tip_dir, path).read_text())
        try:
            take_out(tip)
            if ast.dump(tip) != ast.dump(base):
                raise Seam("something else changed")
        except Seam as e:
            print(f"SEAM STOP {path}: {e}")
            failed = True
        else:
            print(f"seam ok {path}: only the H-256 change(s)")
    if failed:
        return 1
    print("SEAM OK: four production files, each changed only as written")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
