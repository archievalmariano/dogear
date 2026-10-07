"""The S2 hosting pieces that can be checked without a network or a Workers runtime."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _jsonc(path: Path) -> dict:
    text = re.sub(r"^\s*//.*$", "", path.read_text(encoding="utf-8"), flags=re.M)
    return json.loads(text)


class SiteTests(unittest.TestCase):
    def test_route_vectors_are_current(self):
        run = subprocess.run(["python3", str(ROOT / "tools" / "route_vectors.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_bindings_point_at_the_right_buckets(self):
        prod = _jsonc(ROOT / "hosting" / "dogear-site" / "wrangler.jsonc")
        staging = _jsonc(ROOT / "hosting" / "dogear-site" / "staging" / "wrangler.jsonc")
        self.assertEqual((prod["name"], prod["r2_buckets"][0]["bucket_name"]), ("dogear-site", "dogear-issues"))
        self.assertEqual((staging["name"], staging["r2_buckets"][0]["bucket_name"]),
                         ("dogear-staging", "dogear-issues-staging"))
        self.assertEqual(prod["r2_buckets"][0]["binding"], staging["r2_buckets"][0]["binding"])
        self.assertNotIn("goto", json.dumps([prod, staging]))  # shares nothing with GOTO


class TargetTests(unittest.TestCase):
    def test_the_two_profiles(self):
        from dogear.publish.cli import TARGETS
        staging, production = TARGETS["staging"], TARGETS["production"]
        self.assertEqual((staging["store"], staging["mode"], staging["base_url"]),
                         ("r2:dogear-issues-staging", "test", "https://dogear-staging.pages.dev"))
        self.assertEqual(staging["dataset"].name, "staging-s2.json")
        self.assertEqual((production["store"], production["mode"], production["base_url"]),
                         ("r2:dogear-issues", "production", "https://dogear.archievalmariano.com"))
        self.assertEqual((production["dataset"].name, production["policy"].name, production["affinity"].name),
                         ("literary-dates.json", "publication-policy.json", "affinity.json"))
        for key in ("dataset", "affinity"):  # private: the dogear-editorial checkout at dataset/
            self.assertEqual(production[key].relative_to(ROOT).parts[:2], ("dataset", "data"))
            self.assertEqual(staging[key].relative_to(ROOT).parts[0], "fixtures")
        self.assertNotIn("fixtures", str(production["dataset"]) + str(production["policy"]))

    def run_cli(self, *argv) -> int:
        import os
        import tempfile
        from unittest import mock
        from dogear.cli import main
        env = {k: v for k, v in os.environ.items() if not k.startswith("DOGEAR_R2_")}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            return main([*argv[:-1], "publish", "--pubdata", tmp, *argv[-1]])

    def test_target_refuses_loose_settings_and_needs_credentials(self):
        self.assertEqual(self.run_cli(["--target", "staging", "--store", "/tmp/x", "status"]), 4)
        self.assertEqual(self.run_cli(["--target", "production", "--mode", "test", "status"]), 4)
        self.assertEqual(self.run_cli("--dataset", "fixtures/staging-s2.json", ["--target", "production", "status"]), 4)
        self.assertEqual(self.run_cli(["--target", "staging", "status"]), 4)  # no R2 credentials in the environment
        self.assertEqual(self.run_cli(["status"]), 4)  # neither --store nor --target
        self.assertEqual(self.run_cli("--affinity", "fixtures/staging-affinity.json", ["--target", "staging", "status"]), 4)

    def test_production_refuses_without_the_editorial_checkout(self):
        import dogear.publish.cli as pcli
        from unittest import mock
        absent = ROOT / "no-such-checkout" / "data"
        prod = dict(pcli.TARGETS["production"], dataset=absent / "literary-dates.json", affinity=absent / "affinity.json")
        with mock.patch.dict(pcli.TARGETS, {"production": prod}):
            self.assertEqual(self.run_cli(["--target", "production", "status"]), 4)

    def test_target_refuses_an_endpoint_override_before_any_request(self):
        import os
        import tempfile
        from unittest import mock
        from dogear.cli import main
        env = {"DOGEAR_R2_ACCOUNT_ID": "0" * 32, "DOGEAR_R2_ACCESS_KEY_ID": "AKID", "DOGEAR_R2_SECRET_ACCESS_KEY": "s",
               "DOGEAR_R2_ENDPOINT": "https://elsewhere.example"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env), \
                mock.patch("http.client.HTTPSConnection") as https, mock.patch("http.client.HTTPConnection") as http:
            for target in ("staging", "production"):
                self.assertEqual(main(["publish", "--target", target, "--pubdata", tmp, "status"]), 4)
            self.assertFalse(https.called or http.called)


class FixtureTests(unittest.TestCase):
    def test_s2_fixture_is_current_synthetic_and_valid(self):
        run = subprocess.run(["python3", str(ROOT / "tools" / "staging_s2_fixture.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        from dogear.dataset import load_dataset
        ds = load_dataset(ROOT / "fixtures" / "staging-s2.json")
        self.assertEqual(len(ds.records), 15)
        for r in ds.records:
            self.assertTrue(r.id.startswith("s2-") and r.publishable, r.id)
            self.assertEqual(r.approval.by, "synthetic-fixture-not-editorial")
            self.assertEqual({link.url for link in r.links}, {"https://example.org/"})
        self.assertTrue(all(r.notes.startswith("Synthetic") for r in ds.records))  # never an editorial record


class WorkflowTests(unittest.TestCase):
    WORKFLOWS = ROOT / ".github" / "workflows"

    def test_no_expression_is_expanded_inside_a_shell_command(self):
        """Inputs reach commands only through env (script injection, GitHub's guidance)."""
        for wf in sorted(self.WORKFLOWS.glob("*.yml")):
            in_run = False
            for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("run:") or stripped.startswith("- run:"):
                    in_run = True
                    body = stripped.split("run:", 1)[1]
                    self.assertNotIn("${{", body, f"{wf.name}:{n}")
                    in_run = body.strip() in ("|", ">")
                    indent = len(line) - len(line.lstrip())
                    continue
                if in_run:
                    if stripped and len(line) - len(line.lstrip()) <= indent:
                        in_run = False
                    else:
                        self.assertNotIn("${{", line, f"{wf.name}:{n}")

    def test_publication_runs_only_through_the_reusable_job(self):
        reusable = (self.WORKFLOWS / "_dogear-publish.yml").read_text(encoding="utf-8")
        self.assertIn("run: python tools/run_publish.py", reusable)
        self.assertIn("environment: ${{ inputs.target }}", reusable)
        self.assertIn("group: dogear-publication-${{ inputs.target }}", reusable)
        self.assertIn("cancel-in-progress: false", reusable)
        for name in ("dogear-stage.yml", "dogear-promote.yml", "dogear-operate.yml"):
            text = (self.WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("uses: ./.github/workflows/_dogear-publish.yml", text, name)
            self.assertNotIn("dogear.cli", text, name)
            self.assertNotIn("DOGEAR_R2_SECRET", text, name)
            if "schedule:" in text:
                self.assertIn("vars.PUBLICATION_ENABLED == 'true'", text, name)
        self.assertNotIn("schedule:", (self.WORKFLOWS / "dogear-operate.yml").read_text(encoding="utf-8"))

    def jobs(self):
        text = (self.WORKFLOWS / "_dogear-publish.yml").read_text(encoding="utf-8")
        gate = text.split("\n  editorial-gate:\n", 1)[1].split("\n  publish:\n", 1)[0]
        publish = text.split("\n  publish:\n", 1)[1]
        return text, gate, publish

    def test_credentials_are_minimal(self):
        text, gate, publish = self.jobs()
        # The gate holds only the read-only editorial key; nothing it checks out keeps credentials.
        self.assertIn("environment: editorial-gate", gate)
        self.assertIn("if: ${{ inputs.target == 'production' }}", gate)
        self.assertIn("ssh-key: ${{ secrets.EDITORIAL_DEPLOY_KEY }}", gate)
        for absent in ("PUBLICATION_DATA_DEPLOY_KEY", "DOGEAR_R2_", "ssh-key: ${{ secrets.PUBLICATION"):
            self.assertNotIn(absent, gate)
        self.assertEqual(gate.count("persist-credentials: false"), gate.count("uses: actions/checkout@v4"))
        # Publish: the write key only for the publication data; the editorial checkout keeps none.
        data = publish.split("name: Checkout publication data", 1)[1].split("- name:", 1)[0]
        self.assertIn("ssh-key: ${{ secrets.PUBLICATION_DATA_DEPLOY_KEY }}", data)
        self.assertNotIn("token:", data)
        editorial = publish.split("name: Checkout editorial dataset", 1)[1].split("- name:", 1)[0]
        self.assertIn("if: ${{ inputs.target == 'production' }}", editorial)  # staging never reads it
        self.assertIn("ref: ${{ needs.editorial-gate.outputs.editorial_sha }}", editorial)
        self.assertIn("persist-credentials: false", editorial)
        source = publish.split("name: Checkout source", 1)[1].split("- name:", 1)[0]
        self.assertIn("persist-credentials: false", source)
        ci = (self.WORKFLOWS / "dogear-ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", ci)
        self.assertNotIn("environment:", ci)
        self.assertEqual(ci.count("persist-credentials: false"), ci.count("uses: actions/checkout@v4"))
        for wf in self.WORKFLOWS.glob("*.yml"):
            self.assertIn("permissions:\n  contents: read", wf.read_text(encoding="utf-8"), wf.name)

    def test_public_steps_run_before_any_private_checkout(self):
        _, gate, publish = self.jobs()
        order = [publish.index(name) for name in ("name: Set up Python", "name: Fit checker", "name: Tests (guard)",
                                                    "name: Checkout publication data", "name: Checkout editorial dataset",
                                                    "name: Same commit pair as the gate", "name: Publish")]
        self.assertEqual(order, sorted(order))
        self.assertLess(gate.index("name: Fit checker"), gate.index("name: Checkout editorial dataset"))

    def test_production_needs_the_gate_and_its_exact_pair(self):
        _, gate, publish = self.jobs()
        self.assertIn("needs: editorial-gate", publish)
        self.assertIn("needs.editorial-gate.result == 'success'", publish)
        self.assertIn("run: python tools/editorial_gate.py", gate)
        self.assertIn("public_sha: ${{ steps.pair.outputs.public_sha }}", gate)
        self.assertIn("editorial_sha: ${{ steps.pair.outputs.editorial_sha }}", gate)

    def test_the_pair_check_refuses_any_other_commit(self):
        """Run the workflow's own pair-check script against real repositories."""
        import os
        import tempfile
        _, _, publish = self.jobs()
        block = publish.split("name: Same commit pair as the gate", 1)[1].split("run: |\n", 1)[1].split("\n\n", 1)[0]
        script = "\n".join(line[10:] for line in block.splitlines())
        ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "PATH": os.environ["PATH"]}
        with tempfile.TemporaryDirectory() as tmp:
            def repo(path):
                subprocess.run(["git", "init", "-q", path], check=True, env=ident)
                subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty", "-m", "a"], check=True, env=ident)
                first = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty", "-m", "b"], check=True, env=ident)
                second = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                return first, second
            pub_old, pub_head = repo(tmp)
            ed_old, ed_head = repo(os.path.join(tmp, "dataset"))

            def ok(gate_public, gate_editorial):
                env = dict(ident, GATE_PUBLIC=gate_public, GATE_EDITORIAL=gate_editorial)
                return subprocess.run(["bash", "-c", script], cwd=tmp, env=env, capture_output=True).returncode == 0

            self.assertTrue(ok(pub_head, ed_head))
            self.assertFalse(ok(pub_old, ed_head))  # a different public commit
            self.assertFalse(ok(pub_head, ed_old))  # a different editorial commit
            self.assertFalse(ok("", ed_head))       # no gate output
            self.assertFalse(ok(pub_head, ""))

    def test_publication_data_repo_is_checked_as_owner_repo_before_checkout(self):
        """Run the workflow's own check script (bash) against good and bad values."""
        text = (self.WORKFLOWS / "_dogear-publish.yml").read_text(encoding="utf-8")
        self.assertLess(text.index("name: Check configuration"), text.index("repository: ${{ vars.PUBLICATION_DATA_REPO }}"))
        block = text.split("name: Check configuration", 1)[1].split("run: |\n", 1)[1].split("\n\n", 1)[0]
        script = "\n".join(line[10:] for line in block.splitlines())

        def ok(target, repo):
            return subprocess.run(["bash", "-c", script], env={"TARGET": target, "DATA_REPO": repo, "PATH": "/usr/bin:/bin"},
                                  capture_output=True, text=True).returncode == 0

        self.assertTrue(ok("staging", "archievalmariano/dogear-publication-data-staging"))
        self.assertTrue(ok("production", "archievalmariano/dogear-publication-data"))
        for target, repo in (("staging", "dogear-publication-data-staging"), ("production", "dogear-publication-data"),
                             ("staging", "archievalmariano/dogear-publication-data"),
                             ("production", "archievalmariano/dogear-publication-data-staging"),
                             ("staging", "archievalmariano/project-goto-publication-data"), ("staging", ""),
                             ("prod", "archievalmariano/dogear-publication-data")):
            self.assertFalse(ok(target, repo), (target, repo))

    def test_workflows_are_inert_unless_this_is_the_repository_root(self):
        """GitHub reads only a repository root's .github. Inside project-goto (dogear/ is
        a subdirectory) these workflows are inert; in the standalone dogear repository
        they are live, which is the intent of the split."""
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
        if not top or Path(top).resolve() == ROOT.resolve():
            self.skipTest("standalone repository: these workflows are live")
        self.assertTrue(ROOT.resolve().is_relative_to(Path(top).resolve()) if hasattr(Path, "is_relative_to")
                        else str(ROOT.resolve()).startswith(str(Path(top).resolve())))
        self.assertFalse((Path(top) / ".github" / "workflows" / "dogear-promote.yml").exists())

class RunPublishTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import run_publish
        self.rp = run_publish
        self.base = {"DOGEAR_TARGET": "staging", "PUBDATA": "/tmp/pd", "DOGEAR_GIT": "1"}

    def argv(self, **env):
        return self.rp.build_argv(dict(self.base, **env))[4:]

    def test_free_text_is_one_argument(self):
        reason = 'fix "x"; rm -rf / $(id) `id` && echo'
        out = self.argv(DOGEAR_OP="correct", DOGEAR_WEEK="2027-03-01", DOGEAR_EXPECT_REV="0", DOGEAR_REASON=reason)
        self.assertEqual(out[-2:], ["--reason", reason])
        self.assertEqual(out[:7], ["--target", "staging", "--pubdata", "/tmp/pd", "--git", "--public-log", "correct"])

    def test_bad_inputs_are_refused(self):
        bad = [dict(DOGEAR_OP="publish-everything"), dict(DOGEAR_OP="promote", DOGEAR_TARGET="prod"),
               dict(DOGEAR_OP="promote", DOGEAR_WEEK="2027-03-01"),
               dict(DOGEAR_OP="correct", DOGEAR_WEEK="2027-3-1", DOGEAR_EXPECT_REV="0", DOGEAR_REASON="r"),
               dict(DOGEAR_OP="correct", DOGEAR_WEEK="2027-03-01", DOGEAR_EXPECT_REV="-1", DOGEAR_REASON="r"),
               dict(DOGEAR_OP="correct", DOGEAR_WEEK="2027-03-01", DOGEAR_EXPECT_REV="0"),
               dict(DOGEAR_OP="correct", DOGEAR_WEEK="2027-03-01", DOGEAR_EXPECT_REV="0", DOGEAR_REASON="a\nb"),
               dict(DOGEAR_OP="abandon", DOGEAR_TXN="--force"), dict(DOGEAR_OP="restore", DOGEAR_WEEK="2027-03-01"),
               dict(DOGEAR_OP="stage", DOGEAR_WEEK="--now=2027-03-01")]
        for env in bad:
            with self.assertRaises(self.rp.BadInput, msg=env):
                self.argv(**env)
        with self.assertRaises(self.rp.BadInput):
            self.rp.build_argv({"DOGEAR_TARGET": "staging", "DOGEAR_OP": "status"})  # no PUBDATA

    def test_launch_and_optional_rollback_revision(self):
        self.assertEqual(self.argv(DOGEAR_OP="launch")[-2:], ["launch", "--first-publication"])
        self.assertEqual(self.argv(DOGEAR_OP="rollback", DOGEAR_WEEK="2027-03-01", DOGEAR_REASON="r")[-5:],
                         ["rollback", "--week", "2027-03-01", "--reason", "r"])
        self.assertIn("--rev", self.argv(DOGEAR_OP="rollback", DOGEAR_WEEK="2027-03-01", DOGEAR_REV="0",
                                         DOGEAR_REASON="r"))


if __name__ == "__main__":
    unittest.main()
