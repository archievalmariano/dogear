"""The deploy-key check (tools/key_check.sh, against a fake git that plays GitHub),
its workflow, and the write discipline every workflow and tool keeps on the
unprotected private repositories: no force, no deletion, dry runs only outside
the publisher's one fast-forward push."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "tools" / "key_check.sh"

FAKE_GIT = r'''#!PYTHON
import json, os, re, sys
args = sys.argv[1:]
op = "clone" if "clone" in args else "push" if "push" in args else None
if op is None:
    sys.exit(0)
if os.environ.get("FAKE_NETWORK"):
    sys.stderr.write("ssh: connect to host github.com port 22: Connection timed out\nfatal: Could not read from remote repository.\n")
    sys.exit(128)
if op == "push" and "--dry-run" not in args:
    sys.stderr.write("fake git: a real push was attempted\n")
    sys.exit(99)
key = re.search(r"-i (\S+)", os.environ["GIT_SSH_COMMAND"]).group(1)
ident = open(key).read().split()[0]
url = next(a for a in args if a.startswith("git@github.com:"))
repo = url.split("/", 1)[1][:-4]
access = json.loads(os.environ["FAKE_ACCESS"]).get(ident, {}).get(repo, "none")
if op == "clone" and access in ("read", "write"):
    sys.exit(0)
if op == "push" and access == "write":
    sys.exit(0)
if op == "push" and access == "read":
    sys.stderr.write("ERROR: The key you are authenticating with has been marked as read only.\nfatal: Could not read from remote repository.\n")
else:
    sys.stderr.write("ERROR: Repository not found.\nfatal: Could not read from remote repository.\n")
sys.exit(128)
'''

ED, PROD, STG = "dogear-editorial", "dogear-publication-data", "dogear-publication-data-staging"
# What the design installs: each key on its one repository.
DESIGN = {"EDKEY": {ED: "read"}, "PRODKEY": {PROD: "write"}, "STGKEY": {STG: "write"}}
SENTINEL = "SENTINEL-SECRET-MATERIAL (stands in for a private key)"


class KeyCheckScriptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        fake = self.bin / "git"
        fake.write_text(FAKE_GIT.replace("PYTHON", sys.executable))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def run_check(self, environment, editorial="", data="", access=None, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "ENVIRONMENT": environment, "OWNER": "archievalmariano",
               "EDITORIAL_KEY": f"{editorial} {SENTINEL}" if editorial else "",
               "DATA_KEY": f"{data} {SENTINEL}" if data else "",
               "FAKE_ACCESS": json.dumps(DESIGN if access is None else access), **extra}
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.assertNotIn("SENTINEL-SECRET-MATERIAL", out)  # no key material, ever
        self.assertNotIn("a real push was attempted", out)
        return r.returncode, out

    def test_the_designed_keys_pass_in_each_environment(self):
        for environment, editorial, data, lines in (("editorial-gate", "EDKEY", "", 4),
                                                    ("production", "EDKEY", "PRODKEY", 6),
                                                    ("staging", "", "STGKEY", 4)):
            code, out = self.run_check(environment, editorial, data)
            self.assertEqual(code, 0, out)
            self.assertEqual(out.count("\nok "), lines, out)
            self.assertNotIn("FAIL", out)
        _, out = self.run_check("production", "EDKEY", "PRODKEY")
        for line in (f"EDITORIAL_DEPLOY_KEY -> {ED}: read", f"EDITORIAL_DEPLOY_KEY -> {PROD}: none",
                     f"EDITORIAL_DEPLOY_KEY -> {STG}: none", f"PUBLICATION_DATA_DEPLOY_KEY -> {PROD}: write",
                     f"PUBLICATION_DATA_DEPLOY_KEY -> {STG}: none", f"PUBLICATION_DATA_DEPLOY_KEY -> {ED}: none"):
            self.assertIn(line, out)

    def assert_fails(self, *args, **kwargs):
        code, out = self.run_check(*args, **kwargs)
        self.assertNotEqual(code, 0, out)
        self.assertIn("FAIL", out)
        return out

    def test_a_writable_editorial_key_fails(self):
        self.assert_fails("editorial-gate", "EDKEY", access=dict(DESIGN, EDKEY={ED: "write"}))

    def test_a_key_that_reaches_another_repository_fails(self):
        self.assert_fails("production", "EDKEY", "PRODKEY", access=dict(DESIGN, PRODKEY={PROD: "write", STG: "read"}))
        self.assert_fails("staging", "", "STGKEY", access=dict(DESIGN, STGKEY={STG: "write", ED: "read"}))
        self.assert_fails("production", "EDKEY", "PRODKEY", access=dict(DESIGN, EDKEY={ED: "read", PROD: "write"}))

    def test_the_wrong_key_in_an_environment_fails(self):
        self.assert_fails("staging", "", "PRODKEY")     # production's data key in staging
        self.assert_fails("production", "EDKEY", "STGKEY")
        self.assert_fails("staging", "EDKEY", "STGKEY")  # staging must hold no editorial key
        self.assert_fails("editorial-gate", "EDKEY", "PRODKEY")  # the gate holds no data key

    def test_a_missing_key_fails(self):
        self.assert_fails("production", "EDKEY", "")
        self.assert_fails("production", "", "PRODKEY")
        self.assert_fails("editorial-gate", "", "")
        self.assert_fails("staging", "", "")

    def test_a_read_only_data_key_fails(self):
        self.assert_fails("staging", "", "STGKEY", access=dict(DESIGN, STGKEY={STG: "read"}))

    def test_a_network_failure_is_an_error_not_no_access(self):
        out = self.assert_fails("editorial-gate", "EDKEY", FAKE_NETWORK="1")
        self.assertIn(": error (expected none)", out)

    def test_an_unknown_environment_is_refused(self):
        code, _ = self.run_check("prod", "EDKEY", "PRODKEY")
        self.assertEqual(code, 2)


class KeyCheckWorkflowTests(unittest.TestCase):
    TEXT = (WORKFLOWS / "dogear-key-check.yml").read_text(encoding="utf-8")

    def test_manual_only_one_environment_from_main(self):
        on = self.TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", on)
        for trigger in ("push:", "pull_request", "schedule:", "workflow_call", "workflow_run"):
            self.assertNotIn(trigger, on)
        self.assertIn("options: [editorial-gate, staging, production]", on)
        self.assertEqual(self.TEXT.count("environment: ${{ inputs.environment }}"), 1)
        self.assertEqual(self.TEXT.count("runs-on:"), 1)  # one job, one Environment
        self.assertIn("if: ${{ github.ref == 'refs/heads/main' }}", self.TEXT)

    def test_holds_only_the_two_deploy_keys_and_checks_out_only_the_source(self):
        self.assertEqual(sorted(set(re.findall(r"secrets\.([A-Z_]+)", self.TEXT))),
                         ["EDITORIAL_DEPLOY_KEY", "PUBLICATION_DATA_DEPLOY_KEY"])
        self.assertNotIn("DOGEAR_R2", self.TEXT)
        self.assertNotIn("vars.", self.TEXT)
        self.assertEqual(self.TEXT.count("uses: actions/checkout@v4"), 1)
        self.assertNotIn("repository:", self.TEXT)
        self.assertNotIn("ssh-key:", self.TEXT)
        self.assertIn("persist-credentials: false", self.TEXT)
        self.assertIn("run: bash tools/key_check.sh", self.TEXT)
        self.assertNotIn("run_publish", self.TEXT)


class WriteDisciplineTests(unittest.TestCase):
    """The private repositories have no branch protection (GitHub Free): no workflow
    or tool may force, delete or mirror, and the only real push is the publisher's."""

    FORBIDDEN = re.compile(r"--force|force-with-lease|--delete|--mirror|--prune|\bpush\s+-f\b|\+HEAD:|\+refs/|"
                           r"update-ref\s+-d|branch\s+-D")

    def files(self):
        yield from sorted(WORKFLOWS.glob("*.yml"))
        yield from sorted((ROOT / "tools").glob("*.sh"))

    def test_no_workflow_or_shell_tool_forces_or_deletes(self):
        for path in self.files():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                self.assertIsNone(self.FORBIDDEN.search(line), f"{path.name}:{n}: {line.strip()}")

    def test_shell_pushes_are_dry_runs(self):
        for path in self.files():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bgit\b.*\spush(\s|$)", line) and not line.lstrip().startswith("#"):
                    self.assertIn("--dry-run", line, f"{path.name}:{n}")

    def test_the_only_push_in_code_is_the_fast_forward_one(self):
        pushes = []
        for path in sorted((ROOT / "dogear").rglob("*.py")) + sorted((ROOT / "tools").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            pushes += [(path.name, m) for m in re.findall(r'\(\s*"push"[^)]*\)', text)]
            self.assertNotRegex(text, r'\[\s*"git"[^\]]*"push"', path.name)  # no direct subprocess push
        self.assertEqual(pushes, [("pubdata.py", '("push", "-q", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")')])

    def test_the_publication_data_checkout_is_main_and_pushes_need_a_refspec(self):
        text = (WORKFLOWS / "_dogear-publish.yml").read_text(encoding="utf-8")
        data = text.split("name: Checkout publication data", 1)[1].split("- name:", 1)[0]
        self.assertIn("ref: main", data)
        ident = text.split("name: Publication-data identity", 1)[1].split("- name:", 1)[0]
        self.assertIn("git config push.default nothing", ident)
        self.assertIn("git config --unset-all remote.origin.push", ident)


class InstallScriptTests(unittest.TestCase):
    """tools/install_deploy_keys.sh is run by the owner; these check its shape."""
    PATH_ = ROOT / "tools" / "install_deploy_keys.sh"
    TEXT = PATH_.read_text(encoding="utf-8")

    def test_refuses_to_run_unattended(self):
        r = subprocess.run(["bash", str(self.PATH_)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("interactive terminal", r.stdout)

    def test_the_private_key_file_is_only_generated_stored_and_deleted(self):
        uses = [line.strip() for line in self.TEXT.splitlines() if re.search(r'"\$work/\$file"(?!\.pub)', line)]
        self.assertEqual(len(uses), 3, uses)
        self.assertTrue(uses[0].startswith("ssh-keygen -q -t ed25519 -N \"\" "))
        self.assertTrue(uses[1].startswith('gh secret set "$secret" --repo "$SOURCE" --env "$env" < "$work/$file"'))
        self.assertTrue(uses[2].startswith('rm -f "$work/$file"'))
        self.assertNotIn("set -x", self.TEXT)
        self.assertNotIn("deploy-key add", self.TEXT.split("set -euo pipefail", 1)[1])  # web UI, not gh

    def test_the_designed_placement(self):
        self.assertIn('install_key editorial dogear-editorial true "dogear-ci editorial (read-only)" \\\n'
                      "  EDITORIAL_DEPLOY_KEY editorial-gate production\n", self.TEXT)
        self.assertIn('install_key pubdata-production dogear-publication-data false "dogear-ci production publisher" \\\n'
                      "  PUBLICATION_DATA_DEPLOY_KEY production\n", self.TEXT)
        self.assertIn('install_key pubdata-staging dogear-publication-data-staging false "dogear-ci staging publisher" \\\n'
                      "  PUBLICATION_DATA_DEPLOY_KEY staging\n", self.TEXT)


if __name__ == "__main__":
    unittest.main()
