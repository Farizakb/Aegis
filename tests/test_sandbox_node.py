from agent.nodes.sandbox import sandbox_node
from agent.state import ProposedFix, SandboxResult
from tests.test_agent_nodes import make_incident


class FakeExecutor:
    def __init__(self, result: SandboxResult):
        self._result = result
        self.last_patch = None
        self.last_target = None

    async def run(self, *, patch: str, target_file: str) -> SandboxResult:
        self.last_patch = patch
        self.last_target = target_file
        return self._result


async def test_sandbox_node_returns_result():
    sandbox_result = SandboxResult(
        passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=1500.0
    )
    executor = FakeExecutor(sandbox_result)
    fix = ProposedFix(
        description="cap memory",
        patch="--- a/mock_app/faults/memory_leak.py\n+++ b/mock_app/faults/memory_leak.py\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    state = {
        "incident": make_incident(),
        "proposed_fix": fix,
        "sandbox_result": None,
    }

    result = await sandbox_node(state, executor)

    assert result["sandbox_result"] == sandbox_result
    assert executor.last_patch == fix.patch
    assert executor.last_target == fix.target_file


async def test_sandbox_node_with_failed_result():
    sandbox_result = SandboxResult(
        passed=False, exit_code=1, stdout="", stderr="patch failed", duration_ms=200.0
    )
    executor = FakeExecutor(sandbox_result)
    fix = ProposedFix(description="bad fix", patch="garbage", target_file="mock_app/main.py")
    state = {"incident": make_incident(), "proposed_fix": fix, "sandbox_result": None}

    result = await sandbox_node(state, executor)

    assert result["sandbox_result"].passed is False
    assert result["sandbox_result"].exit_code == 1
