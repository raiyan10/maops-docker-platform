"""Docker-free unit tests for scripts/lint/check_dockerfile.py's pure
parsing/validation logic (Day 6: three-stage Distroless Dockerfile with a
Debian-security `security-patch` overlay stage).

scripts/ is not an importable package (matching this project's existing
convention), so this module is loaded via importlib.util.spec_from_file_location
against the real file. No Docker build is ever run here - this only
exercises the checker's own text-parsing/validation logic against
synthetic Dockerfile text, never the real docker/app/Dockerfile's build
behavior (that's `make build`/`make dockerfile-check`'s job, not
unittest's - see .claude/CLAUDE.md).

The ADD checksum/URL literals below match the real, checked-in
security/runtime-patches.lock exactly - check_remote_add_policy() always
loads that real repository file (it takes no injectable path), so a
fixture using different values would fail for a reason unrelated to the
behavior under test.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

LIBSSL_URL = (
    "https://snapshot.debian.org/archive/debian-security/20260825T185058Z/"
    "pool/updates/main/o/openssl/libssl3t64_3.5.7-1~deb13u2_amd64.deb"
)
LIBSSL_DEB_SHA256 = "916f7f40b34a06e6ebfaefcdab331bff458328411da672598f126a760472467d"


def load_check_dockerfile() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_dockerfile.py"
    spec = importlib.util.spec_from_file_location("check_dockerfile_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_DOCKERFILE = f"""\
# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder
WORKDIR /app
COPY app/ ./app/
RUN mkdir -p /data && chown 10001:10001 /data

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS security-patch
ADD --checksum=sha256:{LIBSSL_DEB_SHA256} \\
    {LIBSSL_URL} \\
    /tmp/libssl3t64.deb
RUN mkdir -p /patch-root/var/lib/dpkg/status.d && \\
    dpkg-deb -x /tmp/libssl3t64.deb /patch-root

FROM gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea
ARG VERSION=0.0.0-unset
LABEL org.opencontainers.image.title="maops-docker-platform"
WORKDIR /app
COPY --from=builder /app/app ./app/
COPY --from=builder --chown=10001:10001 /data /data
COPY --from=security-patch /patch-root/usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/lib/x86_64-linux-gnu/libssl.so.3
COPY --from=security-patch /patch-root/usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/lib/x86_64-linux-gnu/libcrypto.so.3
COPY --from=security-patch /patch-root/var/lib/dpkg/status.d/libssl3t64 /var/lib/dpkg/status.d/libssl3t64
COPY --from=security-patch /patch-root/var/lib/dpkg/status.d/libssl3t64.md5sums /var/lib/dpkg/status.d/libssl3t64.md5sums
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

    def test_valid_dockerfile_splits_into_three_stages(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        self.assertEqual(len(stages), 3)

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertTrue(all(instr for _, instr, _ in instructions))


class CheckFromTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_three_stage_from_passes(self) -> None:
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
        self.assertIn("exactly 3 FROM", findings[0].message)

    def test_wrong_final_digest_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea",
            "sha256:" + "0" * 64,
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("does not match the approved pin" in f.message for f in findings))

    def test_wrong_builder_digest_is_rejected(self) -> None:
        """Replaces every occurrence, so this also exercises the
        security-patch stage's reuse of the same builder digest."""
        bad = VALID_DOCKERFILE.replace(
            "sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
            "sha256:" + "1" * 64,
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        matches = [f for f in findings if "does not match the approved pin" in f.message]
        self.assertEqual(len(matches), 2)

    def test_non_digest_pinned_from_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder",
            "python:3.13-slim AS builder",
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("not digest-pinned" in f.message for f in findings))

    def test_latest_tag_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder",
            "python:latest@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder",
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

    def test_missing_as_security_patch_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS security-patch",
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("AS security-patch" in f.message for f in findings))

    def test_wrong_security_patch_stage_name_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace("AS security-patch", "AS oops")
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_from(instructions)
        self.assertTrue(any("security-patch" in f.message and "got 'oops'" in f.message for f in findings))


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

    def test_run_in_security_patch_stage_is_allowed(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        stages = self.module.split_stages(instructions)
        patch_runs = [instr for _, instr, _ in stages[1] if instr == "RUN"]
        self.assertTrue(patch_runs)
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


class CheckRemoteAddPolicyTests(unittest.TestCase):
    """Day 6: exactly one remote ADD is permitted - the security-patch
    stage's checksum-pinned Debian-security fetch, verified against the
    real security/runtime-patches.lock."""

    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def _findings(self, text: str) -> list:
        instructions = self.module.parse_instructions(text)
        stages = self.module.split_stages(instructions)
        return self.module.check_remote_add_policy(instructions, stages)

    def test_valid_pinned_add_passes(self) -> None:
        self.assertEqual(self._findings(VALID_DOCKERFILE), [])

    def test_mismatched_checksum_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(LIBSSL_DEB_SHA256, "f" * 64)
        findings = self._findings(bad)
        self.assertTrue(any("LIBSSL_DEB_SHA256" in f.message for f in findings))

    def test_mismatched_url_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(LIBSSL_URL, LIBSSL_URL.replace("libssl3t64", "libssl3t64-evil"))
        findings = self._findings(bad)
        self.assertTrue(any("LIBSSL_URL" in f.message for f in findings))

    def test_missing_checksum_flag_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(f"--checksum=sha256:{LIBSSL_DEB_SHA256} \\\n    ", "")
        findings = self._findings(bad)
        self.assertTrue(any("--checksum=sha256" in f.message for f in findings))

    def test_remote_add_outside_security_patch_stage_is_rejected(self) -> None:
        bad = VALID_DOCKERFILE.replace(
            "RUN mkdir -p /data && chown 10001:10001 /data",
            "RUN mkdir -p /data && chown 10001:10001 /data\n"
            f"ADD --checksum=sha256:{LIBSSL_DEB_SHA256} {LIBSSL_URL} /tmp/other.deb",
        )
        findings = self._findings(bad)
        self.assertTrue(any("only permitted in the security-patch stage" in f.message for f in findings))

    def test_no_add_at_all_is_fine_for_this_check(self) -> None:
        """This check only polices ADD instructions that exist - whether a
        patch is required at all is a vulnerability-policy question
        (scripts/security/check_trivy_report.py), not this checker's job."""
        text = (
            "FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a "
            "AS builder\nWORKDIR /app\n"
        )
        self.assertEqual(self._findings(text), [])


class CheckSecurityPatchPayloadCopiedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_dockerfile()

    def test_valid_dockerfile_copies_all_required_payload(self) -> None:
        instructions = self.module.parse_instructions(VALID_DOCKERFILE)
        self.assertEqual(self.module.check_security_patch_payload_copied(instructions), [])

    def test_no_copy_from_security_patch_is_rejected(self) -> None:
        bad = "\n".join(
            line
            for line in VALID_DOCKERFILE.splitlines()
            if "--from=security-patch" not in line
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_security_patch_payload_copied(instructions)
        self.assertTrue(findings)
        self.assertIn("no COPY --from=security-patch", findings[0].message)

    def test_missing_one_destination_is_rejected(self) -> None:
        bad = "\n".join(
            line
            for line in VALID_DOCKERFILE.splitlines()
            if "libcrypto.so.3" not in line
        )
        instructions = self.module.parse_instructions(bad)
        findings = self.module.check_security_patch_payload_copied(instructions)
        self.assertTrue(findings)
        self.assertIn("libcrypto.so.3", findings[0].message)


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
        all_findings += self.module.check_remote_add_policy(instructions, stages)
        all_findings += self.module.check_security_patch_payload_copied(instructions)
        all_findings += self.module.check_no_secret_vars(instructions)
        all_findings += self.module.check_workdir(instructions)
        all_findings += self.module.check_exec_form_runtime_command(instructions)
        all_findings += self.module.check_no_privileged_concepts(instructions)
        self.assertEqual([str(f) for f in all_findings], [])


if __name__ == "__main__":
    unittest.main()
