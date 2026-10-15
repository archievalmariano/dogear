"""The publish workflow's routing (PUBLISHING §4b): the workflow's own scripts, run
with bash over every case. Unattended production (the production-scheduled
environment, no reviewer) only for a first-attempt schedule event on main of this
repository, from exactly dogear-stage.yml with stage-next or dogear-promote.yml
with promote; everything else is the reviewed production environment, or fails."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "_dogear-publish.yml"
REPO = "archievalmariano/dogear"
STAGE = f"{REPO}/.github/workflows/dogear-stage.yml@refs/heads/main"
PROMOTE = f"{REPO}/.github/workflows/dogear-promote.yml@refs/heads/main"
OPERATE = f"{REPO}/.github/workflows/dogear-operate.yml@refs/heads/main"
STAGING_OPS = f"{REPO}/.github/workflows/dogear-staging-ops.yml@refs/heads/main"
OPS = ("status", "reconcile", "stage-next", "stage", "promote", "launch", "correct", "rollback", "restore",
       "hold", "resume", "abandon")


def step_script(text: str, name: str) -> str:
    """The `run: |` body of the step called ``name``, dedented."""
    block = text.split(f"- name: {name}\n", 1)[1].split("run: |\n", 1)[1]
    out = []
    for line in block.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 8:
            break
        out.append(line)
    indent = min(len(line) - len(line.lstrip()) for line in out if line.strip())
    return "\n".join(line[indent:] for line in out).rstrip() + "\n"


TEXT = WORKFLOW.read_text(encoding="utf-8")
ROUTE = step_script(TEXT, "Route")
ASSERT = step_script(TEXT, "Assert the route")


def run(script: str, env: dict) -> tuple:
    """(exit code, {output: value}) of a step script under bash, as the runner runs it."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        out.write_text("")
        proc = subprocess.run(["bash", "-e", "-c", script], env=dict(env, GITHUB_OUTPUT=str(out), PATH=os.environ["PATH"]),
                              capture_output=True, text=True)
        outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
        return proc.returncode, outputs


def route(target="production", op="promote", event="schedule", attempt="1", ref="refs/heads/main",
          caller=PROMOTE, repo=REPO) -> tuple:
    code, out = run(ROUTE, {"TARGET": target, "OP": op, "EVENT": event, "ATTEMPT": attempt, "REF": ref,
                            "CALLER": caller, "REPO": repo})
    return (out.get("environment"), out.get("unattended")) if code == 0 else ("FAIL", None)


def asserted(environment, unattended, target="production", op="promote", event="schedule", attempt="1",
             ref="refs/heads/main") -> bool:
    code, _ = run(ASSERT, {"ENVIRONMENT": environment, "UNATTENDED": unattended, "TARGET": target, "OP": op,
                           "EVENT": event, "ATTEMPT": attempt, "REF": ref})
    return code == 0


class RouteTests(unittest.TestCase):
    def test_only_the_two_exact_scheduled_pairs_run_unattended_in_production(self):
        self.assertEqual(route(op="stage-next", caller=STAGE), ("production-scheduled", "1"))
        self.assertEqual(route(op="promote", caller=PROMOTE), ("production-scheduled", "1"))

    def test_everything_else_is_the_reviewed_production_environment(self):
        reviewed = ("production", "0")
        cases = {
            "rerun": dict(attempt="2"), "third attempt": dict(attempt="3"), "no attempt": dict(attempt=""),
            "dispatch": dict(event="workflow_dispatch"), "api dispatch": dict(event="repository_dispatch"),
            "push": dict(event="push"), "workflow_call event": dict(event="workflow_call"),
            "another branch": dict(ref="refs/heads/feature"), "a tag": dict(ref="refs/tags/v1"),
            "a pull request": dict(ref="refs/pull/1/merge"), "a fork": dict(repo="someone/dogear"),
            "operate": dict(caller=OPERATE), "staging ops": dict(caller=STAGING_OPS),
            "caller on another branch": dict(caller=PROMOTE.replace("refs/heads/main", "refs/heads/x")),
            "stage caller, promote op": dict(caller=STAGE), "promote caller, stage-next op": dict(op="stage-next"),
            "empty caller": dict(caller=""), "caller suffix": dict(caller=PROMOTE + "x"),
            "caller prefix": dict(caller="x" + PROMOTE),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.assertEqual(route(**change), reviewed)
        for op in OPS:
            if op in ("stage-next", "promote"):
                continue
            for caller in (STAGE, PROMOTE, OPERATE):
                with self.subTest(op=op, caller=caller):
                    self.assertEqual(route(op=op, caller=caller), reviewed)

    def test_staging_rehearses_the_unattended_rules_in_its_own_environment(self):
        self.assertEqual(route(target="staging", op="stage-next", caller=STAGE), ("staging", "1"))
        self.assertEqual(route(target="staging", op="promote"), ("staging", "1"))
        for change in (dict(attempt="2"), dict(event="workflow_dispatch"), dict(ref="refs/heads/x"), dict(op="status")):
            with self.subTest(change):
                self.assertEqual(route(target="staging", **change), ("staging", "0"))

    def test_a_bad_target_fails(self):
        for target in ("", "prod", "production-scheduled", "staging ", "Production"):
            with self.subTest(target=target):
                self.assertEqual(route(target=target), ("FAIL", None))


class RouteAssertionTests(unittest.TestCase):
    """The publish job's first step, before any secret: fails closed on any mismatch,
    including a rerun of only that job (which reuses the route's attempt-1 outputs)."""

    def test_the_routed_cases_pass(self):
        self.assertTrue(asserted("production-scheduled", "1"))
        self.assertTrue(asserted("production-scheduled", "1", op="stage-next"))
        self.assertTrue(asserted("production", "0", event="workflow_dispatch", op="correct"))
        self.assertTrue(asserted("staging", "0", target="staging", event="workflow_dispatch"))
        self.assertTrue(asserted("staging", "1", target="staging"))

    def test_any_mismatch_fails_closed(self):
        cases = {
            "rerun of the publish job": dict(environment="production-scheduled", unattended="1", attempt="2"),
            "dispatch": dict(environment="production-scheduled", unattended="1", event="workflow_dispatch"),
            "another branch": dict(environment="production-scheduled", unattended="1", ref="refs/heads/x"),
            "another op": dict(environment="production-scheduled", unattended="1", op="correct"),
            "hold": dict(environment="production-scheduled", unattended="1", op="hold"),
            "flag missing": dict(environment="production-scheduled", unattended="0"),
            "unknown environment": dict(environment="production-other", unattended="0"),
            "empty environment": dict(environment="", unattended="0"),
            "staging target, production env": dict(environment="production", unattended="0", target="staging"),
            "production target, staging env": dict(environment="staging", unattended="0"),
            "staging target, scheduled env": dict(environment="production-scheduled", unattended="1", target="staging"),
            "reviewed production unattended": dict(environment="production", unattended="1"),
            "bad flag": dict(environment="production", unattended="yes"),
            "staging unattended on a rerun": dict(environment="staging", unattended="1", target="staging", attempt="2"),
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.assertFalse(asserted(**case))

    def test_the_assertion_is_the_publish_jobs_first_step_before_any_secret(self):
        publish = TEXT.split("\n  publish:\n", 1)[1]
        steps = publish.split("\n    steps:\n", 1)[1]
        self.assertTrue(steps.lstrip().startswith("# First") or steps.lstrip().startswith("- name: Assert the route"))
        self.assertLess(steps.index("- name: Assert the route"), steps.index("secrets."))
        self.assertEqual(steps.split("- name: ")[1].split("\n", 1)[0], "Assert the route")
        self.assertNotIn("secrets.", steps.split("- name: Assert the route", 1)[1].split("- name:", 1)[0])


if __name__ == "__main__":
    unittest.main()
