import asyncio

from fastapi.testclient import TestClient

from agent.state import HitlChoice, PolicyDecision, PolicyVerdict, ProposedFix
from hitl.gate import ApprovalGate
from hitl.web import create_hitl_app
from tests.test_agent_nodes import make_incident


def test_get_pending_empty():
    gate = ApprovalGate()
    app = create_hitl_app(gate)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No pending" in resp.text


def test_post_decision_resolves_gate():
    gate = ApprovalGate()
    app = create_hitl_app(gate)
    client = TestClient(app)
    incident = make_incident()
    fix = ProposedFix(description="fix", patch="+x\n", target_file="mock_app/main.py")
    verdict = PolicyVerdict(decision=PolicyDecision.allow)

    async def _seed():
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        gate._pending[incident.incident_id] = fut
        gate._items[incident.incident_id] = {
            "incident_id": incident.incident_id,
            "title": incident.title,
            "target_file": fix.target_file,
            "description": fix.description,
            "patch": fix.patch,
            "policy": verdict.model_dump(),
        }
        return fut

    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    fut = loop.run_until_complete(_seed())

    resp = client.post(f"/decision/{incident.incident_id}", data={"choice": "approve", "note": ""}, follow_redirects=False)
    assert resp.status_code == 303

    assert fut.done()
    assert fut.result().choice == HitlChoice.approve
    loop.close()


def test_get_pending_shows_item():
    gate = ApprovalGate()
    gate._items["inc-99"] = {
        "incident_id": "inc-99",
        "title": "test incident",
        "target_file": "mock_app/main.py",
        "description": "fix something",
        "patch": "+line\n",
        "policy": {},
    }
    app = create_hitl_app(gate)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "inc-99" in resp.text
    assert "test incident" in resp.text
