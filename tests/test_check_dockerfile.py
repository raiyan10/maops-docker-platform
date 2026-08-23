"""Docker-free unit tests for scripts/lint/check_dockerfile.py's pure
parsing/validation logic (Day 4: two-stage Distroless Dockerfile).

scripts/ is not an importable package (matching this project's existing
convention), so this module is loaded via importlib.util.spec_from_file_location
against the real file. No Docker build is ever run here - this only
exercises the checker's own text-parsing/validation logic against
synthetic Dockerfile text, never the real docker/app/Dockerfile's build
behavior (that's `make build`/`make dockerfile-check`'s job, not
unittest's - see .claude/CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_dockerfile() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_dockerfile.py"
    spec = importlib.util.spec_from_file_location("check_dockerfile_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_DOCKERFILE = """\
# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder
WORKDIR /app
COPY app/ ./app/
RUN mkdir -p /data && chown 10001:10001 /data

FROM gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea
ARG VERSION=0.0.0-unset
LABEL org.opencontainers.image.title="maops-docker-platform"
WORKDIR /app
COPY --from=builder /app/app ./app/
COPY --from=builder --chown=10001:10001 /data /data
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \\
    CMD ["/usr/bin/python3.13", "-m", "app.healthcheck"]
ENTRYPOINT ["/usr/bin/python3.13"]
CMD ["-m", "app"]
"""


class ParseAndSplitStagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_dockerfile_splits_into_two_stages(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        self.assertEqual(len(stages), 2)

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertTrue(all(instr for _, instr, _ in instructions))


class CheckFromTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_two_stage_from_passes(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertEqual(self.module.check_from(instructions), [])

    def test_single_stage_is_rejected(self) -> None:
        text = (
            "FROM gcr.io/distroless/python3-debian13:nonroot@sha256:"
            "4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea\n"
        )
        instructions = self.module.parse_instructions(text)
        findings = self.module.check_from(instructions)
        self.assertTrue(findings)
        self.assertIn("exactly 2 FROM", findings[0].message)

    def test_wrong_final_digest_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea",
            "sha256:" + "0" * 64,
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("does not match the approved pin" in f.message for f in findings))

    def test_wrong_builder_digest_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
            "sha256:" + "1" * 64,
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("does not match the approved pin" in f.message for f in findings))

    def test_non_digest_pinned_from_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
            "python:3.13-slim",
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("not digest-pinned" in f.message for f in findings))

    def test_latest_tag_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
            "python:latest@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("latest" in f.message for f in findings))

    def test_missing_as_builder_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder",
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("AS builder" in f.message for f in findings))


class CheckNoRunInFinalStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_dockerfile_has_no_run_in_final_stage(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        self.assertEqual(self.module.check_no_run_in_final_stage(stages), [])

    def test_run_in_final_stage_is_rejected(self) -> None:
        """The Distroless final stage has no shell/coreutils - a RUN there
        would break the build outright, and this must be caught statically
        rather than only discovered as a build failure."""
        bad = VALID_DOCKERFILE.replace("USER 10001:10001", "RUN echo hack\nUSER 10001:10001")
        instructions = self.module.parse_instructions(bad)
        stages = self.module.split_stages(instructions)
        findings = self.module.check_no_run_in_final_stage(stages)
        self.assertTrue(findings)
        self.assertIn("shellless", findings[0].message)

    def test_run_in_builder_stage_is_allowed(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        builder_runs = [instr for _, instr, _ in stages[0] if instr == "RUN"]
        self.assertTrue(builder_runs)
        self.assertEqual(self.module.check_no_run_in_final_stage(stages), [])


class CheckHealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_healthcheck_passes(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertEqual(self.module.check_healthcheck(instructions), [])

    def test_bare_python3_healthcheck_is_rejected(self) -> None:
        """Regression guard: a bare 'python3' HEALTHCHECK CMD depends on
        shell PATH resolution the Distroless final stage cannot perform."""
        bad = VALID_DOCKERFILE.replace(
            'CMD ["/usr/bin/python3.13", "-m", "app.healthcheck"]',
            'CMD ["python3", "-m", "app.healthcheck"]',
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_healthcheck(instructions)
        self.assertTrue(findings)
        self.assertIn("/usr/bin/python3.13", findings[0].message)


class CheckExecFormRuntimeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_entrypoint_passes(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertEqual(self.module.check_exec_form_runtime_command(instructions), [])

    def test_bare_python3_entrypoint_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace('ENTRYPOINT ["/usr/bin/python3.13"]', 'ENTRYPOINT ["python3"]')
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_exec_form_runtime_command(instructions)
        self.assertTrue(any("/usr/bin/python3.13" in f.message for f in findings))

    def test_shell_form_entrypoint_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace('ENTRYPOINT ["/usr/bin/python3.13"]', "ENTRYPOINT /usr/bin/python3.13")
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_exec_form_runtime_command(instructions)
        self.assertTrue(any("not exec form" in f.message for f in findings))

    def test_missing_entrypoint_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace('ENTRYPOINT ["/usr/bin/python3.13"]\n', "")
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_exec_form_runtime_command(instructions)
        self.assertTrue(any("no ENTRYPOINT" in f.message for f in findings))


class CheckUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_user_passes(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertEqual(self.module.check_user(instructions), [])

    def test_root_user_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace("USER 10001:10001", "USER root")
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_user(instructions)
        self.assertTrue(any("root" in f.message for f in findings))


class FullValidDockerfileIntegrationTest(unittest.TestCase):
    """Discriminating-power guard: the full valid fixture must produce zero
    findings across every check this module runs, not just the ones
    exercised individually above."""

    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_dockerfile_passes_every_check(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        all_findings = []
        all_findings += self.module.check_from(instructions)
        all_findings += self.module.check_no_run_in_final_stage(stages)
        all_findings += self.module.check_user(instructions)
        all_findings += self.module.check_healthcheck(instructions)
        all_findings += self.module.check_no_sudo(instructions)
        all_findings += self.module.check_no_remote_add(instructions)
        all_findings += self.module.check_no_secret_vars(instructions)
        all_findings += self.module.check_workdir(instructions)
        all_findings += self.module.check_exec_form_runtime_command(instructions)
        all_findings += self.module.check_no_privileged_concepts(instructions)
        self.assertEqual([str(f) for f in all_findings], [])


if __name__ == "__main__":
    unittest.main()
