"""Docker-free unit tests for scripts/ci/check_workflows.py's workflow-
policy logic (Day 6).

Every test exercises the individual check_*()/helper functions directly
against small, fabricated fixture text - never the real committed
`.github/workflows/*.yml` files (those are covered separately by
`make workflow-check`/`make quality` actually running against them). This
mirrors this project's existing pattern of proving a validator's
*discriminating power* against both good and deliberately bad synthetic
input (e.g. `tests/test_check_trivy_report.py`'s policy-evaluation tests),
not merely re-running it against the one already-good real file.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_workflows() -> ModuleType:
    path = REPO_ROOT / "scripts" / "ci" / "check_workflows.py"
    spec = importlib.util.spec_from_file_location("check_workflows_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOOD_CI = """\
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - run: make quality
"""

GOOD_RELEASE = """\
name: Release
on:
  push:
    tags:
      - "v*.*.*"
  workflow_dispatch: {}
permissions:
  contents: read
jobs:
  validate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - run: make release-check
  publish:
    needs: validate
    if: >-
      success() &&
      github.event_name == 'push' &&
      startsWith(github.ref, 'refs/tags/')
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - run: gh release create "$TAG"
"""


class StripCommentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_removes_trailing_comment(self) -> None:
        result = self.module._strip_comments("uses: actions/checkout@abc123 # v7.0.1")
        self.assertNotIn("v7.0.1", result)
        self.assertIn("uses: actions/checkout@abc123", result)

    def test_removes_full_line_comment(self) -> None:
        result = self.module._strip_comments("# Deliberately NOT pull_request_target: ...")
        self.assertNotIn("pull_request_target", result)

    def test_preserves_line_count(self) -> None:
        text = "a: 1\n# comment\nb: 2"
        self.assertEqual(len(self.module._strip_comments(text).splitlines()), 3)


class BlockExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_top_level_block_extracts_nested_lines(self) -> None:
        text = "permissions:\n  contents: read\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
        block = self.module.top_level_block(text, "permissions")
        self.assertEqual([l.strip() for l in block], ["contents: read"])

    def test_top_level_block_stops_at_dedent(self) -> None:
        text = "permissions:\n  contents: read\njobs:\n  x: y\n"
        block = self.module.top_level_block(text, "permissions")
        self.assertNotIn("jobs:", "\n".join(block))

    def test_missing_key_returns_empty(self) -> None:
        self.assertEqual(self.module.top_level_block("a: 1\n", "permissions"), [])

    def test_job_block_extracts_only_that_job(self) -> None:
        block = self.module.job_block(GOOD_RELEASE, "publish")
        joined = "\n".join(block)
        self.assertIn("contents: write", joined)
        self.assertNotIn("validate", joined.split("needs: validate")[-1] if "needs: validate" in joined else joined)

    def test_job_block_missing_job_returns_empty(self) -> None:
        self.assertEqual(self.module.job_block(GOOD_RELEASE, "nonexistent"), [])

    def test_nested_block_finds_key_within_a_block(self) -> None:
        block = self.module.job_block(GOOD_RELEASE, "validate")
        perms = self.module.nested_block(block, "permissions")
        self.assertEqual([l.strip() for l in perms], ["contents: read"])


GOOD_CI_RELEASE_POLICY = """\
name: CI
permissions:
  contents: read
jobs:
  release-policy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - name: Create job-scoped Buildx builder (docker-container driver)
        run: |
          docker buildx create --driver docker-container --name maops-ci-1-1 --use
          docker buildx inspect maops-ci-1-1 --bootstrap
      - name: make release-check
        run: make release-check
      - name: Remove job-scoped Buildx builder
        if: always()
        run: |
          docker buildx rm maops-ci-1-1
"""

GOOD_RELEASE_VALIDATE = """\
name: Release
permissions:
  contents: read
jobs:
  validate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - name: Create job-scoped Buildx builder (docker-container driver)
        run: |
          docker buildx create --driver docker-container --name maops-ci-1-1 --use
          docker buildx inspect maops-ci-1-1 --bootstrap
      - name: make release-check
        run: make release-check
      - name: Remove job-scoped Buildx builder
        if: always()
        run: |
          docker buildx rm maops-ci-1-1
"""


class ListItemsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_splits_top_level_list_items(self) -> None:
        block = ["- name: a", "  run: x", "- name: b", "  run: y"]
        items = self.module.list_items(block)
        self.assertEqual(len(items), 2)
        self.assertIn("name: a", items[0][0])
        self.assertIn("name: b", items[1][0])

    def test_continuation_lines_stay_with_their_item(self) -> None:
        block = ["- name: a", "  run: |", "    line1", "    line2", "- name: b", "  run: z"]
        items = self.module.list_items(block)
        self.assertEqual(len(items), 2)
        self.assertIn("line1", "\n".join(items[0]))
        self.assertIn("line2", "\n".join(items[0]))

    def test_empty_block_returns_no_items(self) -> None:
        self.assertEqual(self.module.list_items([]), [])


class BuildxContainerBuilderBeforeReleaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_ci_release_policy_passes(self) -> None:
        self.assertEqual(
            self.module.check_buildx_container_builder_before_release_check({"ci.yml": GOOD_CI_RELEASE_POLICY}), []
        )

    def test_good_release_validate_passes(self) -> None:
        self.assertEqual(
            self.module.check_buildx_container_builder_before_release_check(
                {"release.yml": GOOD_RELEASE_VALIDATE}
            ),
            [],
        )

    def test_missing_release_policy_job_is_rejected(self) -> None:
        text = "jobs:\n  quality:\n    steps:\n      - run: make quality\n"
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": text})
        self.assertTrue(findings)

    def test_missing_release_check_step_is_rejected(self) -> None:
        bad = GOOD_CI_RELEASE_POLICY.replace(
            "      - name: make release-check\n        run: make release-check\n", ""
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(findings)

    def test_missing_builder_creation_is_rejected(self) -> None:
        bad = GOOD_CI_RELEASE_POLICY.replace(
            "      - name: Create job-scoped Buildx builder (docker-container driver)\n"
            "        run: |\n"
            "          docker buildx create --driver docker-container --name maops-ci-1-1 --use\n"
            "          docker buildx inspect maops-ci-1-1 --bootstrap\n",
            "",
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(any("must create a 'docker-container'" in str(f) for f in findings))

    def test_builder_creation_missing_use_flag_is_rejected(self) -> None:
        bad = GOOD_CI_RELEASE_POLICY.replace(
            "docker buildx create --driver docker-container --name maops-ci-1-1 --use",
            "docker buildx create --driver docker-container --name maops-ci-1-1",
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(any("must create a 'docker-container'" in str(f) for f in findings))

    def test_builder_creation_after_release_check_is_rejected(self) -> None:
        create_step = (
            "      - name: Create job-scoped Buildx builder (docker-container driver)\n"
            "        run: |\n"
            "          docker buildx create --driver docker-container --name maops-ci-1-1 --use\n"
            "          docker buildx inspect maops-ci-1-1 --bootstrap\n"
        )
        release_check_step = "      - name: make release-check\n        run: make release-check\n"
        bad = GOOD_CI_RELEASE_POLICY.replace(create_step, "").replace(
            release_check_step, release_check_step + create_step
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(any("must run before" in str(f) for f in findings))

    def test_missing_cleanup_step_is_rejected(self) -> None:
        bad = GOOD_CI_RELEASE_POLICY.replace(
            "      - name: Remove job-scoped Buildx builder\n"
            "        if: always()\n"
            "        run: |\n"
            "          docker buildx rm maops-ci-1-1\n",
            "",
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(any("must remove its job-scoped Buildx builder" in str(f) for f in findings))

    def test_cleanup_step_missing_always_is_rejected(self) -> None:
        bad = GOOD_CI_RELEASE_POLICY.replace(
            "      - name: Remove job-scoped Buildx builder\n        if: always()\n",
            "      - name: Remove job-scoped Buildx builder\n",
        )
        findings = self.module.check_buildx_container_builder_before_release_check({"ci.yml": bad})
        self.assertTrue(any("if: always()" in str(f) for f in findings))


class RequiredFilesExistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_real_repository_workflow_files_exist(self) -> None:
        """Cross-checks the real committed files this Day 6 branch ships."""
        self.assertEqual(self.module.check_required_files_exist(), [])


class NoPullRequestTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_ci_passes(self) -> None:
        self.assertEqual(self.module.check_no_pull_request_target({"ci.yml": GOOD_CI}), [])

    def test_explanatory_comment_mentioning_it_does_not_false_positive(self) -> None:
        text = self.module._strip_comments("# Deliberately NOT pull_request_target: unsafe for PRs\non:\n  push: {}\n")
        self.assertEqual(self.module.check_no_pull_request_target({"ci.yml": text}), [])

    def test_actual_trigger_key_is_rejected(self) -> None:
        bad = "on:\n  pull_request_target:\n    branches: [main]\n"
        findings = self.module.check_no_pull_request_target({"ci.yml": bad})
        self.assertTrue(findings)


class UsesPinnedToFullShaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_ci_passes(self) -> None:
        self.assertEqual(self.module.check_uses_pinned_to_full_sha({"ci.yml": GOOD_CI}), [])

    def test_floating_major_version_tag_is_rejected(self) -> None:
        bad = "uses: actions/checkout@v4\n"
        self.assertTrue(self.module.check_uses_pinned_to_full_sha({"ci.yml": bad}))

    def test_floating_branch_ref_is_rejected(self) -> None:
        bad = "uses: some/action@main\n"
        self.assertTrue(self.module.check_uses_pinned_to_full_sha({"ci.yml": bad}))

    def test_short_sha_is_rejected(self) -> None:
        bad = "uses: actions/checkout@3d3c42e\n"
        self.assertTrue(self.module.check_uses_pinned_to_full_sha({"ci.yml": bad}))

    def test_full_sha_is_accepted(self) -> None:
        good = "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
        self.assertEqual(self.module.check_uses_pinned_to_full_sha({"ci.yml": good}), [])


class NoContinueOnErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_files_pass(self) -> None:
        self.assertEqual(self.module.check_no_continue_on_error({"ci.yml": GOOD_CI, "release.yml": GOOD_RELEASE}), [])

    def test_continue_on_error_true_is_rejected(self) -> None:
        bad = "steps:\n  - run: make release-check\n    continue-on-error: true\n"
        self.assertTrue(self.module.check_no_continue_on_error({"release.yml": bad}))


class NoManufacturedPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_files_pass(self) -> None:
        self.assertEqual(self.module.check_no_manufactured_pass({"ci.yml": GOOD_CI, "release.yml": GOOD_RELEASE}), [])

    def test_or_true_is_rejected(self) -> None:
        bad = "steps:\n  - run: make release-check || true\n"
        self.assertTrue(self.module.check_no_manufactured_pass({"release.yml": bad}))


class CiPermissionsReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_ci_passes(self) -> None:
        self.assertEqual(self.module.check_ci_permissions_read_only({"ci.yml": GOOD_CI}), [])

    def test_missing_permissions_block_is_rejected(self) -> None:
        bad = "name: CI\non:\n  push: {}\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
        self.assertTrue(self.module.check_ci_permissions_read_only({"ci.yml": bad}))

    def test_write_permission_is_rejected(self) -> None:
        bad = "permissions:\n  contents: write\n"
        self.assertTrue(self.module.check_ci_permissions_read_only({"ci.yml": bad}))

    def test_extra_broader_scope_elsewhere_is_rejected(self) -> None:
        bad = GOOD_CI + "\njobs:\n  other:\n    permissions:\n      issues: write\n"
        self.assertTrue(self.module.check_ci_permissions_read_only({"ci.yml": bad}))


class ReleasePermissionsScopedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_release_passes(self) -> None:
        self.assertEqual(self.module.check_release_permissions_scoped({"release.yml": GOOD_RELEASE}), [])

    def test_workflow_level_write_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace("permissions:\n  contents: read\n", "permissions:\n  contents: write\n", 1)
        self.assertTrue(self.module.check_release_permissions_scoped({"release.yml": bad}))

    def test_validate_job_write_permission_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace(
            "  validate:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
            "  validate:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n",
        )
        self.assertTrue(self.module.check_release_permissions_scoped({"release.yml": bad}))

    def test_missing_publish_job_is_rejected(self) -> None:
        text = "permissions:\n  contents: read\njobs:\n  validate:\n    permissions:\n      contents: read\n"
        self.assertTrue(self.module.check_release_permissions_scoped({"release.yml": text}))

    def test_publish_job_missing_write_permission_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace("    permissions:\n      contents: write\n", "")
        self.assertTrue(self.module.check_release_permissions_scoped({"release.yml": bad}))


class ManualDispatchCannotPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_release_passes(self) -> None:
        self.assertEqual(self.module.check_manual_dispatch_cannot_publish({"release.yml": GOOD_RELEASE}), [])

    def test_publish_referencing_workflow_dispatch_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace(
            "success() &&\n      github.event_name == 'push' &&",
            "success() &&\n      (github.event_name == 'push' || github.event_name == 'workflow_dispatch') &&",
        )
        self.assertTrue(self.module.check_manual_dispatch_cannot_publish({"release.yml": bad}))

    def test_publish_missing_push_event_check_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace("github.event_name == 'push' &&\n      ", "")
        self.assertTrue(self.module.check_manual_dispatch_cannot_publish({"release.yml": bad}))

    def test_publish_missing_tag_ref_check_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace("startsWith(github.ref, 'refs/tags/')", "true")
        self.assertTrue(self.module.check_manual_dispatch_cannot_publish({"release.yml": bad}))

    def test_publish_unconditional_if_missing_success_is_rejected(self) -> None:
        bad = GOOD_RELEASE.replace(
            "if: >-\n      success() &&\n      github.event_name == 'push' &&\n      startsWith(github.ref, 'refs/tags/')",
            "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')",
        )
        # This variant is actually fine w.r.t. success() being present via a
        # different phrasing check - assert the specific missing-substring
        # case instead: an if: with neither success() nor needs-based default.
        bad2 = bad.replace("if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')", "if: always()")
        self.assertTrue(self.module.check_manual_dispatch_cannot_publish({"release.yml": bad2}))


class RequiredTriggersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_files_pass(self) -> None:
        self.assertEqual(
            self.module.check_required_triggers({"ci.yml": GOOD_CI, "release.yml": GOOD_RELEASE}), []
        )

    def test_ci_missing_pull_request_trigger_is_rejected(self) -> None:
        bad = "on:\n  push:\n    branches: [main]\n"
        self.assertTrue(self.module.check_required_triggers({"ci.yml": bad}))

    def test_ci_missing_push_trigger_is_rejected(self) -> None:
        bad = "on:\n  pull_request:\n    branches: [main]\n"
        self.assertTrue(self.module.check_required_triggers({"ci.yml": bad}))

    def test_release_missing_tag_pattern_is_rejected(self) -> None:
        bad = "on:\n  push:\n    tags:\n      - 'release-*'\n  workflow_dispatch: {}\n"
        self.assertTrue(self.module.check_required_triggers({"release.yml": bad}))

    def test_release_missing_workflow_dispatch_is_rejected(self) -> None:
        bad = "on:\n  push:\n    tags:\n      - 'v*.*.*'\n"
        self.assertTrue(self.module.check_required_triggers({"release.yml": bad}))


class NoRegistryPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_files_pass(self) -> None:
        self.assertEqual(
            self.module.check_no_registry_publication({"ci.yml": GOOD_CI, "release.yml": GOOD_RELEASE}), []
        )

    def test_docker_push_is_rejected(self) -> None:
        bad = "steps:\n  - run: docker push maops-docker-platform:latest\n"
        self.assertTrue(self.module.check_no_registry_publication({"release.yml": bad}))

    def test_ghcr_reference_is_rejected(self) -> None:
        bad = "steps:\n  - run: docker tag x ghcr.io/org/x\n"
        self.assertTrue(self.module.check_no_registry_publication({"release.yml": bad}))

    def test_docker_login_is_rejected(self) -> None:
        bad = "steps:\n  - run: docker login -u x -p y\n"
        self.assertTrue(self.module.check_no_registry_publication({"release.yml": bad}))


class NoDay7PlusToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_good_files_pass(self) -> None:
        self.assertEqual(
            self.module.check_no_day7_plus_tooling({"ci.yml": GOOD_CI, "release.yml": GOOD_RELEASE}), []
        )

    def test_cosign_is_rejected(self) -> None:
        bad = "steps:\n  - run: cosign sign maops-docker-platform\n"
        self.assertTrue(self.module.check_no_day7_plus_tooling({"release.yml": bad}))

    def test_kubectl_is_rejected(self) -> None:
        bad = "steps:\n  - run: kubectl apply -f deploy.yaml\n"
        self.assertTrue(self.module.check_no_day7_plus_tooling({"release.yml": bad}))

    def test_terraform_is_rejected(self) -> None:
        bad = "steps:\n  - run: terraform apply\n"
        self.assertTrue(self.module.check_no_day7_plus_tooling({"release.yml": bad}))


class MainDeterminismTests(unittest.TestCase):
    """The self-reference proof this project's own review brief asked
    about: running against the real, committed files must produce a clean
    pass, with no dependency on any GITHUB_* environment variable."""

    def setUp(self) -> None:
        self.module = load_check_workflows()

    def test_real_committed_workflows_pass_every_check(self) -> None:
        texts = self.module.read_workflow_files()
        all_findings = []
        for check in self.module.CHECKS:
            all_findings.extend(check(texts))
        self.assertEqual([str(f) for f in all_findings], [])

    def test_main_reads_no_github_environment_variables(self) -> None:
        """Static source-scan proof: this module never reads os.environ at
        all (so it certainly never depends on a GITHUB_* runtime variable) -
        every check operates purely on the two files' own committed
        content. Deliberately does not merely grep for the substring
        "GITHUB_", since this module's own docstring legitimately
        *discusses* GITHUB_* env vars while explaining why it avoids them."""
        source = (REPO_ROOT / "scripts" / "ci" / "check_workflows.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)
        self.assertNotIn("import os", source)


if __name__ == "__main__":
    unittest.main()
