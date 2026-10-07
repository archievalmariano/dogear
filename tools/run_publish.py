"""The workflows' only way into the publisher: builds the `dogear publish` argv
from environment variables and runs it, never through a shell, so a free-text
input (a correction's reason) is always exactly one argument.

    DOGEAR_TARGET=staging|production   DOGEAR_OP=<op>   [DOGEAR_WEEK, DOGEAR_REV,
    DOGEAR_EXPECT_REV, DOGEAR_REASON, DOGEAR_TXN]   PUBDATA=<checkout>   [DOGEAR_GIT=1]

Exits with the publisher's own code (docs: dogear/publish/cli.py).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = {
    "status": (), "reconcile": (), "stage-next": (), "promote": (), "resume": (),
    "stage": ("week",), "launch": (), "correct": ("week", "expect_rev", "reason"),
    "rollback": ("week", "reason"), "restore": ("week", "rev"), "abandon": ("txn",),
}
OPTIONAL = {"rollback": ("rev",)}
TXN = re.compile(r"^[0-9]{8}T[0-9]{6}-[a-z]+-[0-9a-f]{12}$")


class BadInput(Exception):
    pass


def build_argv(env: dict) -> list:
    target, op = env.get("DOGEAR_TARGET", ""), env.get("DOGEAR_OP", "")
    if target not in ("staging", "production"):
        raise BadInput("DOGEAR_TARGET must be staging or production")
    if op not in OPS:
        raise BadInput(f"DOGEAR_OP must be one of {', '.join(sorted(OPS))}")
    pubdata = env.get("PUBDATA", "")
    if not pubdata:
        raise BadInput("PUBDATA is required")
    argv = [sys.executable, "-m", "dogear.cli", "publish", "--target", target, "--pubdata", pubdata]
    if env.get("DOGEAR_GIT") == "1":
        argv.append("--git")
    argv.append("--public-log")  # Actions logs of a public repository are public
    argv.append(op)
    values = {name: (env.get(f"DOGEAR_{name.upper()}") or "").strip() for name in
              ("week", "rev", "expect_rev", "reason", "txn")}
    wanted = OPS[op] + OPTIONAL.get(op, ())
    for name, value in values.items():
        if value and name not in wanted:
            raise BadInput(f"{op} does not take {name}")
    for name in wanted:
        value = values[name]
        if not value:
            if name in OPTIONAL.get(op, ()):
                continue
            raise BadInput(f"{op} needs {name}")
        if name == "week":
            try:
                if dt.date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                raise BadInput("week must be YYYY-MM-DD") from None
        elif name in ("rev", "expect_rev") and not re.fullmatch(r"[0-9]{1,4}", value):
            raise BadInput(f"{name} must be a whole number")
        elif name == "txn" and not TXN.match(value):
            raise BadInput("txn is not a transaction id")
        elif name == "reason" and (len(value) > 300 or any(ord(c) < 32 for c in value)):
            raise BadInput("reason must be one line of at most 300 characters")
        argv += [f"--{name.replace('_', '-')}", value]
    if op == "launch":
        argv.append("--first-publication")
    return argv


def main() -> int:
    try:
        argv = build_argv(dict(os.environ))
    except BadInput as err:
        print(f"error: {err}", file=sys.stderr)
        return 4
    # Only the operation and target: a reason, week or txn may follow in argv.
    print(f"running: dogear publish {argv[argv.index('--public-log') + 1]} --target {argv[argv.index('--target') + 1]}",
          flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
