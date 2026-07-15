from pathlib import Path

from apply.applier import PatchApplier
from agent.nodes.apply import apply_node
from agent.state import PolicyDecision, PolicyVerdict, ProposedFix
from policy.engine import PolicyEngine
from tests.test_agent_nodes import make_incident


def test_applier_writes_patch_and_creates_backup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "mock_app" / "faults" / "memory_leak.py"
    target.parent.mkdir(parents=True)
    target.write_text("original content\n")

    backup_dir = tmp_path / "backups"
    applier = PatchApplier(repo_root=repo, backup_dir=backup_dir)

    applier.apply(target_file="mock_app/faults/memory_leak.py", patch="patched content\n")

    assert target.read_text() == "patched content\n"
    assert (backup_dir / "memory_leak.py").read_text() == "original content\n"


def test_applier_rollback_restores_original(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "mock_app" / "faults" / "memory_leak.py"
    target.parent.mkdir(parents=True)
    target.write_text("original content\n")

    backup_dir = tmp_path / "backups"
    applier = PatchApplier(repo_root=repo, backup_dir=backup_dir)

    applier.apply(target_file="mock_app/faults/memory_leak.py", patch="patched content\n")
    applier.rollback(target_file="mock_app/faults/memory_leak.py")

    assert target.read_text() == "original content\n"


class FakeApplier:
    def __init__(self):
        self.applied = []

    def apply(self, *, target_file: str, patch: str) -> None:
        self.applied.append((target_file, patch))


async def test_apply_node_calls_applier():
    applier = FakeApplier()
    engine = PolicyEngine()
    fix = ProposedFix(
        description="cap growth",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    verdict = PolicyVerdict(decision=PolicyDecision.needs_approval, violated_rules=["irreversibility_gate"])
    state = {"incident": make_incident(), "proposed_fix": fix, "policy_verdict": verdict}

    result = await apply_node(state, applier, engine)

    assert result == {}
    assert applier.applied == [("mock_app/faults/memory_leak.py", "+cap = 100\n")]
