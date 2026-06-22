import asyncio

from agent.nodes.hitl import hitl_node
from agent.state import HitlChoice, HitlDecision, PolicyDecision, PolicyVerdict, ProposedFix
from hitl.gate import ApprovalGate
from tests.test_agent_nodes import make_incident


async def test_gate_resolve_completes_future():
    gate = ApprovalGate()
    incident = make_incident()
    fix = ProposedFix(description="fix", patch="+x\n", target_file="mock_app/main.py")
    verdict = PolicyVerdict(decision=PolicyDecision.allow)

    async def _approve_after_delay():
        await asyncio.sleep(0.01)
        gate.resolve(incident.incident_id, HitlChoice.approve, note="lgtm")

    asyncio.get_event_loop().create_task(_approve_after_delay())
    decision = await gate.request_approval(incident=incident, fix=fix, verdict=verdict)

    assert decision.choice == HitlChoice.approve
    assert decision.note == "lgtm"


async def test_gate_list_pending_shows_item():
    gate = ApprovalGate()
    incident = make_incident()
    fix = ProposedFix(description="fix", patch="+x\n", target_file="mock_app/main.py")
    verdict = PolicyVerdict(decision=PolicyDecision.allow)

    async def _request():
        await gate.request_approval(incident=incident, fix=fix, verdict=verdict)

    task = asyncio.create_task(_request())
    await asyncio.sleep(0.01)

    pending = gate.list_pending()
    assert len(pending) == 1
    assert pending[0]["incident_id"] == incident.incident_id

    gate.resolve(incident.incident_id, HitlChoice.reject)
    await task


class FakeGate:
    def __init__(self, decision: HitlDecision):
        self._decision = decision

    async def request_approval(self, *, incident, fix, verdict) -> HitlDecision:
        return self._decision


async def test_hitl_node_returns_decision():
    decision = HitlDecision(choice=HitlChoice.approve)
    gate = FakeGate(decision)
    fix = ProposedFix(description="fix", patch="+x\n", target_file="mock_app/main.py")
    verdict = PolicyVerdict(decision=PolicyDecision.needs_approval)
    state = {
        "incident": make_incident(),
        "proposed_fix": fix,
        "policy_verdict": verdict,
        "hitl_decision": None,
    }

    result = await hitl_node(state, gate)

    assert result["hitl_decision"] == decision
