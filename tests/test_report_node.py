from agent.nodes.report import report_node
from agent.state import (
    HitlChoice,
    HitlDecision,
    IncidentReport,
    NodeUsage,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    SandboxResult,
    TriageResult,
)
from stream.schema import FaultKind
from tests.test_agent_nodes import make_incident


class FakeSink:
    def __init__(self):
        self.reports: list[IncidentReport] = []

    def emit(self, report: IncidentReport) -> None:
        self.reports.append(report)


def _base_state(**overrides):
    state = {
        "incident": make_incident(),
        "triage": TriageResult(
            fault_kind=FaultKind.memory_leak,
            root_cause="leak",
            confidence=0.9,
            reasoning="...",
        ),
        "retrieved_context": [],
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [
            NodeUsage(node="triage", model="m", input_tokens=100, output_tokens=20, latency_ms=500),
            NodeUsage(node="propose", model="m", input_tokens=200, output_tokens=50, latency_ms=800),
        ],
    }
    state.update(overrides)
    return state


async def test_report_outcome_applied():
    sink = FakeSink()
    state = _base_state(
        sandbox_result=SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=0),
        policy_verdict=PolicyVerdict(decision=PolicyDecision.allow),
        hitl_decision=HitlDecision(choice=HitlChoice.approve),
    )
    result = await report_node(state, sink)
    assert result["report"].outcome == Outcome.applied
    assert result["report"].total_input_tokens == 300
    assert result["report"].total_output_tokens == 70
    assert len(sink.reports) == 1


async def test_report_outcome_rejected():
    sink = FakeSink()
    state = _base_state(
        sandbox_result=SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=0),
        policy_verdict=PolicyVerdict(decision=PolicyDecision.allow),
        hitl_decision=HitlDecision(choice=HitlChoice.reject),
    )
    result = await report_node(state, sink)
    assert result["report"].outcome == Outcome.rejected


async def test_report_outcome_blocked():
    sink = FakeSink()
    state = _base_state(
        policy_verdict=PolicyVerdict(decision=PolicyDecision.block, violated_rules=["protected_path"]),
    )
    result = await report_node(state, sink)
    assert result["report"].outcome == Outcome.blocked


async def test_report_outcome_escalated():
    sink = FakeSink()
    state = _base_state(
        sandbox_result=SandboxResult(passed=False, exit_code=1, stdout="", stderr="", duration_ms=0),
        retries=2,
    )
    result = await report_node(state, sink)
    assert result["report"].outcome == Outcome.escalated
