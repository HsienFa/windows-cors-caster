"""GitHub Actions CI safety and coverage regressions."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


class GitHubActionsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.lower_source = cls.source.lower()

    @classmethod
    def _job_source(cls, job_name):
        match = re.search(
            rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-z0-9_-]+:\n|\Z)",
            cls.source,
        )
        if match is None:
            raise AssertionError(f"Workflow job not found: {job_name}")
        return match.group("body")

    def test_triggers_permissions_runner_and_python_version(self):
        self.assertIn("push:\n    branches:\n      - windows-native-compat", self.source)
        self.assertIn("pull_request:\n    branches:\n      - main", self.source)
        self.assertRegex(self.source, r"(?m)^  workflow_dispatch:\s*$")
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertEqual(self.source.count("runs-on: ubuntu-latest"), 2)
        self.assertEqual(self.source.count("runs-on: windows-latest"), 1)
        self.assertEqual(self.source.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(self.source.count("persist-credentials: false"), 3)
        self.assertEqual(self.source.count("uses: actions/setup-python@v5"), 3)
        self.assertEqual(self.source.count('python-version: "3.11"'), 3)

    def test_cmd_checks_run_on_windows_and_skip_safely_elsewhere(self):
        ubuntu_job = self._job_source("application-tests")
        windows_job = self._job_source("windows-native-tests")
        windows_tests = (PROJECT_ROOT / "tests" / "test_windows_compat.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("runs-on: windows-latest", windows_job)
        self.assertIn('python-version: "3.11"', windows_job)
        self.assertIn('PYTHONUTF8: "1"', windows_job)
        self.assertIn('PYTHONIOENCODING: "utf-8"', windows_job)
        self.assertIn("python -m pip install -r requirements.txt", windows_job)
        self.assertIn(
            'python -m unittest discover -s tests -p "test_windows_compat.py"',
            windows_job,
        )
        self.assertIn(
            'python -m unittest discover -s tests -p "test_ntrip_upload_lifecycle.py"',
            windows_job,
        )
        self.assertNotIn("cmd.exe", ubuntu_job.lower())
        self.assertNotIn("continue-on-error", self.lower_source)
        self.assertNotRegex(self.lower_source, r"(?m)\|\|\s*(?:true|exit\s+0)\b")

        self.assertIn("if sys.platform != 'win32':", windows_tests)
        self.assertIn("self.skipTest('requires Windows cmd.exe')", windows_tests)
        self.assertIn("def test_batch_check_mode_does_not_start_python", windows_tests)
        self.assertIn("def test_batch_check_mode_reports_missing_required_paths", windows_tests)
        self.assertIn("with self.subTest(missing=missing):", windows_tests)
        for missing_case in ("venv", "python", "main", "config"):
            self.assertRegex(windows_tests, rf"(?m)^\s+'{missing_case}':")
        self.assertEqual(windows_tests.count("self._require_windows_cmd()"), 2)

    def test_full_required_test_groups_and_syntax_checks_are_present(self):
        required_test_files = (
            "test_windows_compat.py",
            "test_traditional_chinese_ui.py",
            "test_security_regressions.py",
            "test_frontend_offline_privacy.py",
            "test_deployment_and_policy.py",
            "test_ci_workflow.py",
            "test_ntrip_upload_lifecycle.py",
        )
        for test_file in required_test_files:
            self.assertIn(test_file, self.source)
        self.assertIn("python -m pip install -r requirements.txt", self.source)
        self.assertIn("ast.parse(source, filename=relative_path)", self.source)
        self.assertIn("node --check", self.source)

    def test_whitespace_check_uses_feature_base_and_rejects_new_violations(self):
        application_job = self._job_source("application-tests")

        self.assertIn("fetch-depth: 0", application_job)
        self.assertIn(
            "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
            application_job,
        )
        self.assertIn('if [ "$GITHUB_EVENT_NAME" = "pull_request" ]', application_job)
        self.assertIn('base_sha="$PR_BASE_SHA"', application_job)
        self.assertIn('base_sha="$(git merge-base origin/main HEAD)"', application_job)
        self.assertIn('git diff --check "${base_sha}...HEAD"', application_job)
        self.assertNotIn("git show --check", application_job)
        self.assertNotIn("hash-object -t tree", application_job)
        self.assertNotIn("4b825dc642cb6eb9a060e54bf8d69288fbee4904", application_job)

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            clean_path = temporary_root / "clean.txt"
            invalid_path = temporary_root / "invalid.txt"
            clean_path.write_text("value\n", encoding="utf-8")
            invalid_path.write_text("value  \n", encoding="utf-8")

            completed = subprocess.run(
                [
                    "git", "-c", "core.autocrlf=false", "diff", "--no-index",
                    "--check", str(clean_path), str(invalid_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("trailing whitespace", completed.stdout.lower())

    def test_make_target_is_static_and_does_not_create_runtime_state(self):
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
        match = re.search(
            r"(?m)^ci-static-check:\n(?P<body>(?:\t[^\n]*\n)+)",
            makefile,
        )
        self.assertIsNotNone(match)
        body = match.group("body").lower()
        self.assertIn("unittest", body)
        self.assertIn("test_deployment_and_policy.py", body)
        self.assertIn("test_ci_workflow.py", body)
        for forbidden in ("docker", "compose", "prepare-env", "write-config", ".env", " build", " pull", " up"):
            self.assertNotIn(forbidden, body)
        self.assertIn("make ci-static-check python=python", self.lower_source)

    def test_compose_validation_is_config_only_and_covers_all_profiles(self):
        self.assertIn("docker compose version", self.source)
        self.assertGreaterEqual(self.source.count("config --quiet"), 5)
        self.assertIn("-f docker-compose.yml -f docker-compose.prod.yml config --quiet", self.source)
        self.assertIn("for profile in nginx monitoring cache", self.source)
        self.assertIn("--profile nginx --profile monitoring --profile cache config --quiet", self.source)

        prohibited_commands = re.compile(
            r"(?im)^\s*(?:docker\s+compose|docker-compose|docker|make)\s+"
            r"(?:up|start|run|create|build|pull|push|publish)\b"
        )
        self.assertIsNone(prohibited_commands.search(self.source))

    def test_credentials_are_ephemeral_and_never_uploaded_or_printed(self):
        self.assertIn("${{ runner.temp }}", self.source)
        self.assertIn("deployment_config.py prepare-env", self.source)
        self.assertIn("umask 077", self.source)
        self.assertIn('--env-file "$CI_ENV_FILE"', self.source)
        self.assertIn("> /dev/null", self.source)
        self.assertIn("if: ${{ always() }}", self.source)
        self.assertIn('rm -rf -- "$CI_SECRET_DIR"', self.source)
        self.assertNotIn("actions/upload-artifact", self.lower_source)
        self.assertNotIn("secrets.", self.lower_source)
        self.assertNotRegex(self.source, r"(?im)^\s*(?:echo|cat)\s+.*(?:password|secret|token|api[_-]?key)")

    def test_runner_context_is_only_used_in_step_level_env(self):
        lines = self.source.splitlines()
        runner_references = [
            index for index, line in enumerate(lines)
            if "${{ runner." in line
        ]
        self.assertGreater(len(runner_references), 0)

        for index in runner_references:
            line = lines[index]
            indentation = len(line) - len(line.lstrip(" "))
            self.assertEqual(indentation, 10)

            parent = None
            for candidate in reversed(lines[:index]):
                if not candidate.strip():
                    continue
                candidate_indentation = len(candidate) - len(candidate.lstrip(" "))
                if candidate_indentation < indentation:
                    parent = candidate
                    break
            self.assertEqual(parent, "        env:")

    def test_workflow_only_checks_tracking_for_local_runtime_paths(self):
        self.assertIn("git ls-files -z", self.source)
        self.assertNotIn("config.windows.local.ini).read", self.lower_source)
        self.assertNotIn("config.ini).read", self.lower_source)
        self.assertNotIn("actions/cache", self.lower_source)


if __name__ == "__main__":
    unittest.main()
