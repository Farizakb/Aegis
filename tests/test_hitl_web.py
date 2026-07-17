"""HITL web app: brief rendering, decision verb validation, promote (ADR-0005)."""

import asyncio

from fastapi.testclient import TestClient

from agent.actions import ActionType, ProposedAction
from agent.state import PolicyDecision, PolicyVerdict, RemediationPlan, SandboxResult
from hitl.gate import ApprovalGate, build_brief
from hitl.registry import DurableFixRegistry
from hitl.web import create_hitl_app
from tests.test_agent_nodes import make_incident, make_triage


def _plan_with_fix() -> RemediationPlan:
    return RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service, reason="clear leak"),
        durable_fix=ProposedAction(action=ActionType.patch_code, reason="cap growth",
                                   patch="+cap = 100\n", target_file="mock_app/faults/memory_leak.py"),
    )


def _verdict() -> PolicyVerdict:
    return PolicyVerdict(decision=PolicyDecision.needs_approval, violated_rules=["irreversibility_gate"],
                         reasons=["patch_code always requires approval"])


def _sandbox() -> SandboxResult:
    return SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=250.0,
                         before_metrics={"rss_mb": 200.0}, after_metrics={"rss_mb": 40.0})


def _seed_pending(gate: ApprovalGate, incident_id: str, *, with_sandbox: bool = True) -> asyncio.Future:
    incident = make_incident(incident_id=incident_id)
    plan = _plan_with_fix()
    triage = make_triage()
    brief = build_brief(incident=incident, plan=plan, sandbox=_sandbox() if with_sandbox else None,
                        verdict=_verdict(), triage=triage, expires_at=9_999_999_999.0)
    fut: asyncio.Future = asyncio.new_event_loop().create_future()
    gate._pending[incident_id] = fut
    gate._briefs[incident_id] = brief
    gate._contexts[incident_id] = {"incident": incident, "triage": triage, "durable_fix": plan.durable_fix}
    return fut


def test_index_shows_brief_and_durable_fix_section():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    _seed_pending(gate, "i-1")
    registry.file(incident=make_incident(incident_id="i-2"), triage=make_triage(),
                  fix=ProposedAction(action=ActionType.patch_code, reason="fix db pool",
                                    patch="+x\n", target_file="mock_app/faults/db_deadlock.py"))

    app = create_hitl_app(gate, registry)
    client = TestClient(app)
    resp = client.get("/")

    assert resp.status_code == 200
    assert "restart_service" in resp.text          # mitigation action
    assert "200.0" in resp.text and "40.0" in resp.text  # before/after metric values
    assert "irreversibility_gate" in resp.text      # fired rule
    assert "i-2" in resp.text                       # open durable fix entry
    assert "Promote" in resp.text


def test_decision_approve_resolves_gate():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    fut = _seed_pending(gate, "i-1")
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/decision/i-1", data={"choice": "approve", "note": ""}, follow_redirects=False)

    assert resp.status_code == 303
    assert fut.done()
    assert fut.result().choice.value == "approve"


def test_decision_rejects_non_approve_reject_verb():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    fut = _seed_pending(gate, "i-1")
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/decision/i-1", data={"choice": "expired", "note": ""}, follow_redirects=False)

    assert resp.status_code == 400
    assert not fut.done()


def test_promote_filed_fix_succeeds():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    registry.file(incident=make_incident(incident_id="i-3"), triage=make_triage(),
                  fix=ProposedAction(action=ActionType.patch_code, reason="cap growth",
                                    patch="+x\n", target_file="mock_app/faults/memory_leak.py"))
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/promote/i-3", follow_redirects=False)

    assert resp.status_code == 303
    assert registry.has_promotions() is True


def test_promote_from_pending_brief_files_then_promotes():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    _seed_pending(gate, "i-4")
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/promote/i-4", follow_redirects=False)

    assert resp.status_code == 303
    assert registry.has_promotions() is True
    assert registry.list_open() == []


def test_decision_unknown_id_returns_404():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/decision/nope", data={"choice": "approve", "note": ""}, follow_redirects=False)

    assert resp.status_code == 404


def test_promote_unknown_id_returns_404():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    resp = client.post("/promote/nope", follow_redirects=False)

    assert resp.status_code == 404


def test_index_escapes_llm_derived_strings():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    incident = make_incident(incident_id="i-xss", title="<script>alert(1)</script> incident")
    plan = RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service,
                                  reason="<script>alert(1)</script> restart"),
        durable_fix=ProposedAction(action=ActionType.patch_code,
                                   reason="<script>alert(1)</script> patch",
                                   patch="+x\n", target_file="mock_app/faults/memory_leak.py"),
    )
    failing_sandbox = SandboxResult(passed=False, exit_code=1, stdout="", stderr="",
                                    duration_ms=10.0,
                                    failure_reason="<script>alert(1)</script> failure")
    triage = make_triage(root_cause="<script>alert(1)</script> cause")
    brief = build_brief(incident=incident, plan=plan, sandbox=failing_sandbox,
                        verdict=_verdict(), triage=triage, expires_at=9_999_999_999.0)
    fut: asyncio.Future = asyncio.new_event_loop().create_future()
    gate._pending["i-xss"] = fut
    gate._briefs["i-xss"] = brief
    gate._contexts["i-xss"] = {"incident": incident, "triage": triage,
                               "durable_fix": plan.durable_fix}
    registry.file(incident=make_incident(incident_id="i-xss-2",
                                         title="<script>alert(2)</script> open fix"),
                  triage=None,
                  fix=ProposedAction(action=ActionType.patch_code,
                                     reason="<script>alert(2)</script> desc",
                                     patch="+x\n", target_file="mock_app/faults/db_deadlock.py"))

    app = create_hitl_app(gate, registry)
    client = TestClient(app)
    resp = client.get("/")

    assert resp.status_code == 200
    assert "<script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_api_pending_and_durable_fixes():
    gate = ApprovalGate()
    registry = DurableFixRegistry()
    _seed_pending(gate, "i-1")
    registry.file(incident=make_incident(incident_id="i-2"), triage=make_triage(),
                  fix=ProposedAction(action=ActionType.patch_code, reason="fix db pool",
                                    patch="+x\n", target_file="mock_app/faults/db_deadlock.py"))
    app = create_hitl_app(gate, registry)
    client = TestClient(app)

    assert client.get("/api/pending").json()[0]["incident_id"] == "i-1"
    assert client.get("/api/durable-fixes").json()[0]["incident_id"] == "i-2"
