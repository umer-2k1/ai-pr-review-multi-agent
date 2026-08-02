"""The GitHub Action packaging.

The point of these tests is INV-4 at the credential layer. The code has no write
path, but code can be changed by accident; a token that cannot write is a
guarantee that survives a bad edit. So the workflow's `permissions:` block is
asserted here as strictly as any code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parent.parent
ACTION = ROOT / "action.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "review.yml"


def _load(path: Path) -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(path.read_text())  # type: ignore[no-any-return]


class TestWorkflowPermissions:
    def test_the_workflow_declares_permissions_at_all(self) -> None:
        """Omitting the block inherits the repository default, which may be
        write. Silence is not a safe default here."""
        assert "permissions" in _load(WORKFLOW)

    def test_no_permission_is_write(self) -> None:
        """M5's stated criterion: the token has no write scope.

        The agent's autonomy level is 'human reviews output'. A write-scoped
        token would grant the action nothing it uses, and would widen the blast
        radius of any future bug for no benefit.
        """
        perms = _load(WORKFLOW)["permissions"]
        offenders = {k: v for k, v in perms.items() if v != "read"}
        assert offenders == {}, f"write scope granted: {offenders}"

    def test_pull_requests_is_read(self) -> None:
        assert _load(WORKFLOW)["permissions"]["pull-requests"] == "read"

    def test_the_job_has_a_timeout(self) -> None:
        """A hung job costs money and blocks the queue. The orchestrator's
        per-lane deadline is the first defence; this is the backstop."""
        job = _load(WORKFLOW)["jobs"]["review"]
        assert isinstance(job.get("timeout-minutes"), int)
        assert 0 < job["timeout-minutes"] <= 60


class TestActionDefinition:
    def test_action_is_valid_and_named(self) -> None:
        action = _load(ACTION)
        assert action["name"]
        assert action["runs"]["using"] == "composite"

    def test_the_token_input_documents_read_only(self) -> None:
        desc = _load(ACTION)["inputs"]["github-token"]["description"]
        assert "read" in desc.lower()

    def test_offline_mode_is_available_without_a_key(self) -> None:
        """The pipeline must be able to prove itself end to end in CI with no
        credentials — otherwise a missing key silently skips the review."""
        assert "offline" in _load(ACTION)["inputs"]

    def test_the_action_exposes_the_gate_verdict(self) -> None:
        outputs = _load(ACTION)["outputs"]
        assert "verdict" in outputs and "confidence" in outputs and "findings" in outputs

    def test_the_action_does_not_default_to_failing_the_build(self) -> None:
        """This reviewer drafts; it does not gate merges. A noisy first week must
        not block the team — opting in is the user's call."""
        assert _load(ACTION)["inputs"]["fail-on-critical"]["default"] == "false"

    def test_no_step_posts_to_the_pull_request(self) -> None:
        """INV-4 in the packaging, not just the library."""
        text = ACTION.read_text().lower()
        for forbidden in ("gh pr comment", "gh pr review", "/comments", "create_review",
                          "issues/comments", "gh api -x post", "-x post"):
            assert forbidden not in text, f"the action contains a write call: {forbidden}"

    def test_no_job_overrides_the_read_only_permissions(self) -> None:
        """A per-job `permissions:` block silently overrides the top-level one.

        Checked on parsed YAML, not raw text: the workflow's comments explain why
        write scope is withheld, and a naive substring match trips on the
        explanation rather than on a real grant.
        """
        wf = _load(WORKFLOW)
        for name, job in wf["jobs"].items():
            perms = job.get("permissions")
            if perms is None:
                continue
            offenders = {k: v for k, v in perms.items() if v != "read"}
            assert offenders == {}, f"job {name} grants write: {offenders}"
