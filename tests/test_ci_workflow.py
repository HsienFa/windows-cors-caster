"""GitHub Actions CI safety and coverage regressions."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


class GitHubActionsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.lower_source = cls.source.lower()

    def test_triggers_permissions_runner_and_python_version(self):
        self.assertIn("push:\n    branches:\n      - windows-native-compat", self.source)
        self.assertIn("pull_request:\n    branches:\n      - main", self.source)
        self.assertRegex(self.source, r"(?m)^  workflow_dispatch:\s*$")
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertEqual(self.source.count("runs-on: ubuntu-latest"), 2)
        self.assertEqual(self.source.count("uses: actions/checkout@v4"), 2)
        self.assertEqual(self.source.count("persist-credentials: false"), 2)
        self.assertEqual(self.source.count("uses: actions/setup-python@v5"), 2)
        self.assertEqual(self.source.count('python-version: "3.11"'), 2)

    def test_full_required_test_groups_and_syntax_checks_are_present(self):
        required_test_files = (
            "test_windows_compat.py",
            "test_traditional_chinese_ui.py",
            "test_security_regressions.py",
            "test_frontend_offline_privacy.py",
            "test_deployment_and_policy.py",
            "test_ci_workflow.py",
        )
        for test_file in required_test_files:
            self.assertIn(test_file, self.source)
        self.assertIn("python -m pip install -r requirements.txt", self.source)
        self.assertIn("ast.parse(source, filename=relative_path)", self.source)
        self.assertIn("node --check", self.source)
        self.assertIn("git diff --check", self.source)
        self.assertIn("git show --check --format= --no-renames HEAD", self.source)

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
