# Week 3: Safe Execution + Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the agent graph with sandbox testing, policy gating, human-in-the-loop approval, apply/rollback, and a report node — so a proposed fix is proven in isolation, gated, approved by a human, and applied (or rejected/blocked/escalated).

**Architecture:** Five new nodes are added after `propose`. A Docker sandbox validates the patch, a deterministic policy engine gates it, an async approval gate suspends the graph until a human clicks approve/reject in a local web UI, the applier writes the fix to disk, and a report node records the outcome. Three conditional routing functions control the flow (retry loop, policy block, HITL decision).

**Tech Stack:** Docker SDK for Python (`docker>=7.1`), FastAPI (existing), asyncio Futures, LangGraph conditional edges, Pydantic models

## Global Constraints

- Python 3.11+
- All nodes: `async def x_node(state: AgentState, <dep>) -> dict` returning partial state
- DI via `functools.partial` in `build_graph` (existing pattern)
- Tests must pass without Docker/Postgres: new integration tests marked `@pytest.mark.integration`
- Run tests: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp_retrieval_server.py --ignore=tests/test_retrieval_search.py`

---

### Task 1: State Models

**Files:**
- Modify: `agent/state.py`
- Modify: `tests/test_agent_state.py`

**Interfaces:**
- Consumes: existing `FaultKind` from `stream.schema`
- Produces: `SandboxResult`, `PolicyDecision`, `PolicyVerdict`, `HitlChoice`, `HitlDecision`, `Outcome`, `IncidentReport` — used by all subsequent tasks

- [ ] **Step 1: Write failing tests for new models**

```python
# tests/test_agent_state.py (append)
from agent.state import (
    HitlChoice,
    HitlDecision,
    IncidentReport,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    SandboxResult,
)


def test_sandbox_result_validates():
    r = SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=1200.0)
    assert r.passed is True
    assert r.exit_code == 0


def test_policy_verdict_validates():
    v = PolicyVerdict(
        decision=PolicyDecision.needs_approval,
        violated_rules=["blast_radius"],
        reasons=["target outside auto-approved paths"],
    )
    assert v.decision == PolicyDecision.needs_approval
    assert len(v.violated_rules) == 1


def test_hitl_decision_validates():
    d = HitlDecision(choice=HitlChoice.approve, decided_by="local-ui", note="looks good")
    assert d.choice == HitlChoice.approve


def test_incident_report_validates():
    r = IncidentReport(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        outcome=Outcome.applied,
        retries=1,
        sandbox_passed=True,
        total_input_tokens=500,
        total_output_tokens=100,
        total_latency_ms=3200.0,
    )
    assert r.outcome == Outcome.applied
    assert r.retries == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_agent_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'SandboxResult'`

- [ ] **Step 3: Implement the new models**

```python
# agent/state.py — full rewrite
"""Agent state contract: typed models for all graph nodes."""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field

from retrieval.models import RetrievedChunk
from stream.schema import FaultKind, IncidentEvent


class TriageResult(BaseModel):
    fault_kind: FaultKind
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class ProposedFix(BaseModel):
    description: str
    patch: str
    target_file: str | None = None


class NodeUsage(BaseModel):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class SandboxResult(BaseModel):
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float


class PolicyDecision(str, Enum):
    allow = "allow"
    needs_approval = "needs_approval"
    block = "block"


class PolicyVerdict(BaseModel):
    decision: PolicyDecision
    violated_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class HitlChoice(str, Enum):
    approve = "approve"
    reject = "reject"


class HitlDecision(BaseModel):
    choice: HitlChoice
    decided_by: str = "local-ui"
    note: str | None = None


class Outcome(str, Enum):
    applied = "applied"
    rejected = "rejected"
    blocked = "blocked"
    escalated = "escalated"


class IncidentReport(BaseModel):
    incident_id: str
    fault_kind: FaultKind
    outcome: Outcome
    triage_confidence: float | None = None
    retries: int = 0
    sandbox_passed: bool | None = None
    policy_decision: PolicyDecision | None = None
    hitl_choice: HitlChoice | None = None
    applied_target_file: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0


class AgentState(TypedDict):
    incident: IncidentEvent
    triage: TriageResult | None
    retrieved_context: list[RetrievedChunk]
    proposed_fix: ProposedFix | None
    retries: int
    sandbox_result: SandboxResult | None
    policy_verdict: PolicyVerdict | None
    hitl_decision: HitlDecision | None
    report: IncidentReport | None
    usage: list[NodeUsage]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_agent_state.py -v`
Expected: PASS (all tests including existing ones)

- [ ] **Step 5: Update existing tests that build full state dicts to include `report: None`**

In `tests/test_agent_nodes.py` and `tests/test_agent_graph.py`, add `"report": None` to every state dict that initializes `AgentState`.

- [ ] **Step 6: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp_retrieval_server.py --ignore=tests/test_retrieval_search.py`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(state): add Week 3 typed models (SandboxResult, PolicyVerdict, HitlDecision, IncidentReport)"
```

---

### Task 2: Policy Engine + policy_node

**Files:**
- Create: `policy/__init__.py`
- Create: `policy/engine.py`
- Create: `agent/nodes/policy.py`
- Create: `tests/test_policy_engine.py`
- Create: `tests/test_policy_node.py`

**Interfaces:**
- Consumes: `ProposedFix`, `TriageResult`, `IncidentEvent` from `agent.state`; `PolicyVerdict`, `PolicyDecision` from `agent.state`
- Produces: `PolicyEngine.evaluate(fix, triage, incident) -> PolicyVerdict`; `policy_node(state, engine) -> {"policy_verdict": PolicyVerdict}`

- [ ] **Step 1: Write failing tests for PolicyEngine**

```python
# tests/test_policy_engine.py
import pytest

from agent.state import PolicyDecision, ProposedFix
from policy.engine import PolicyEngine


@pytest.fixture
def engine():
    return PolicyEngine()


def _fix(target_file: str, patch_lines: int = 5) -> ProposedFix:
    patch = "\n".join([f"+line{i}" for i in range(patch_lines)])
    return ProposedFix(description="fix", patch=patch, target_file=target_file)


def test_allow_when_target_in_mock_app(engine):
    verdict = engine.evaluate(fix=_fix("mock_app/faults/memory_leak.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.allow
    assert verdict.violated_rules == []


def test_needs_approval_when_target_outside_mock_app(engine):
    verdict = engine.evaluate(fix=_fix("stream/consumer.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.needs_approval
    assert "blast_radius" in verdict.violated_rules


def test_block_when_target_is_protected(engine):
    verdict = engine.evaluate(fix=_fix("agent/graph.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.block
    assert "protected_path" in verdict.violated_rules


def test_needs_approval_when_patch_too_large(engine):
    verdict = engine.evaluate(fix=_fix("mock_app/main.py", patch_lines=100), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.needs_approval
    assert "patch_size" in verdict.violated_rules


def test_block_takes_precedence_over_needs_approval(engine):
    verdict = engine.evaluate(fix=_fix("policy/engine.py", patch_lines=100), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_policy_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'policy'`

- [ ] **Step 3: Implement PolicyEngine**

```python
# policy/__init__.py
(empty)

# policy/engine.py
"""Deterministic pre-dispatch policy rules gating proposed fixes."""

from __future__ import annotations

from agent.state import PolicyDecision, PolicyVerdict, ProposedFix

ALLOWED_PATH_PREFIXES = ("mock_app/",)
PROTECTED_PATHS = ("docker-compose.yml", "policy/", "agent/", ".env", "stream/", "retrieval/")
MAX_PATCH_LINES = 80


class PolicyEngine:
    def evaluate(self, *, fix: ProposedFix, triage, incident) -> PolicyVerdict:
        violated: list[str] = []
        reasons: list[str] = []
        decision = PolicyDecision.allow
        tgt = fix.target_file or ""

        if tgt and not any(tgt.startswith(p) for p in ALLOWED_PATH_PREFIXES):
            decision = PolicyDecision.needs_approval
            violated.append("blast_radius")
            reasons.append(f"target {tgt!r} outside auto-approved paths")

        if any(tgt.startswith(p) for p in PROTECTED_PATHS):
            decision = PolicyDecision.block
            violated.append("protected_path")
            reasons.append(f"{tgt!r} is a protected control-plane path")

        if fix.patch.count("\n") > MAX_PATCH_LINES:
            if decision != PolicyDecision.block:
                decision = PolicyDecision.needs_approval
            violated.append("patch_size")
            reasons.append(f"patch exceeds {MAX_PATCH_LINES} lines")

        return PolicyVerdict(decision=decision, violated_rules=violated, reasons=reasons)
```

- [ ] **Step 4: Run PolicyEngine tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_policy_engine.py -v`
Expected: PASS

- [ ] **Step 5: Write failing test for policy_node**

```python
# tests/test_policy_node.py
from agent.nodes.policy import policy_node
from agent.state import PolicyDecision, ProposedFix
from policy.engine import PolicyEngine
from tests.test_agent_nodes import make_incident


async def test_policy_node_returns_verdict():
    engine = PolicyEngine()
    fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    state = {
        "incident": make_incident(),
        "triage": None,
        "proposed_fix": fix,
        "policy_verdict": None,
    }
    result = await policy_node(state, engine)
    assert result["policy_verdict"].decision == PolicyDecision.allow
```

- [ ] **Step 6: Implement policy_node**

```python
# agent/nodes/policy.py
"""Policy node: gate the proposed fix against deterministic rules."""

from __future__ import annotations

from agent.state import AgentState


async def policy_node(state: AgentState, engine) -> dict:
    verdict = engine.evaluate(
        fix=state["proposed_fix"],
        triage=state.get("triage"),
        incident=state.get("incident"),
    )
    return {"policy_verdict": verdict}
```

- [ ] **Step 7: Run policy_node test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_policy_node.py -v`
Expected: PASS

- [ ] **Step 8: Add `policy` to pyproject.toml packages, commit**

Add `"policy"` to `[tool.setuptools].packages` list.

```bash
git add -A && git commit -m "feat(policy): deterministic policy engine + policy_node"
```

---

### Task 3: Sandbox Executor + sandbox_node

**Files:**
- Create: `sandbox/__init__.py`
- Create: `sandbox/executor.py`
- Create: `sandbox/Dockerfile`
- Create: `sandbox/entrypoint.sh`
- Create: `agent/nodes/sandbox.py`
- Create: `tests/test_sandbox_node.py`
- Modify: `pyproject.toml` (add `docker>=7.1`)

**Interfaces:**
- Consumes: `ProposedFix` from state; `SandboxResult` from `agent.state`
- Produces: `SandboxExecutor.run(patch, target_file) -> SandboxResult`; `sandbox_node(state, executor) -> {"sandbox_result": SandboxResult}`

- [ ] **Step 1: Write failing test for sandbox_node (with FakeExecutor)**

```python
# tests/test_sandbox_node.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sandbox_node.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.nodes.sandbox'`

- [ ] **Step 3: Implement sandbox_node**

```python
# agent/nodes/sandbox.py
"""Sandbox node: run proposed patch in an isolated Docker container."""

from __future__ import annotations

from agent.state import AgentState


async def sandbox_node(state: AgentState, executor) -> dict:
    fix = state["proposed_fix"]
    result = await executor.run(patch=fix.patch, target_file=fix.target_file)
    return {"sandbox_result": result}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sandbox_node.py -v`
Expected: PASS

- [ ] **Step 5: Implement SandboxExecutor (real Docker SDK)**

```python
# sandbox/__init__.py
(empty)

# sandbox/executor.py
"""Docker-based sandbox: apply patch, run pytest in an isolated container."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from agent.state import SandboxResult

IMAGE = "aegis-sandbox:latest"
TIMEOUT_S = 60


class SandboxExecutor:
    def __init__(self, docker_client, image: str = IMAGE, timeout_s: int = TIMEOUT_S):
        self._docker = docker_client
        self._image = image
        self._timeout_s = timeout_s

    async def run(self, *, patch: str, target_file: str) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, patch, target_file)

    def _run_sync(self, patch: str, target_file: str) -> SandboxResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
            f.write(patch)
            patch_path = f.name

        start = time.monotonic()
        container = None
        try:
            container = self._docker.containers.run(
                self._image,
                command=["sh", "/sandbox/entrypoint.sh", target_file],
                volumes={patch_path: {"bind": "/in/patch.diff", "mode": "ro"}},
                network_disabled=True,
                mem_limit="256m",
                detach=True,
            )
            result = container.wait(timeout=self._timeout_s)
            exit_code = result["StatusCode"]
            logs = container.logs(stdout=True, stderr=True).decode()
            stdout = logs
            stderr = ""
        except Exception as exc:
            exit_code = 1
            stdout = ""
            stderr = str(exc)
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            if container:
                container.remove(force=True)
            Path(patch_path).unlink(missing_ok=True)

        return SandboxResult(
            passed=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )
```

- [ ] **Step 6: Create sandbox Dockerfile and entrypoint**

```dockerfile
# sandbox/Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
COPY pyproject.toml ./
RUN pip install --no-cache-dir .[dev]

COPY mock_app ./mock_app
COPY stream ./stream
COPY agent ./agent
COPY retrieval ./retrieval
COPY tools ./tools
COPY policy ./policy
COPY tests ./tests

RUN git init && git add -A && git commit -m "sandbox base"

COPY sandbox/entrypoint.sh /sandbox/entrypoint.sh
RUN chmod +x /sandbox/entrypoint.sh
```

```bash
#!/bin/sh
# sandbox/entrypoint.sh
# Usage: entrypoint.sh <target_file>
# Applies /in/patch.diff and runs pytest

set -e
cd /repo
git apply /in/patch.diff 2>&1 || { echo "PATCH_APPLY_FAILED"; exit 1; }
pytest -q tests/ 2>&1
```

- [ ] **Step 7: Add `docker>=7.1` to pyproject.toml dependencies, add `sandbox` to packages**

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat(sandbox): Docker executor + sandbox_node + Dockerfile"
```

---

### Task 4: Patch Applier + apply_node

**Files:**
- Create: `apply/__init__.py`
- Create: `apply/applier.py`
- Create: `agent/nodes/apply.py`
- Create: `tests/test_apply.py`

**Interfaces:**
- Consumes: `ProposedFix` from state
- Produces: `PatchApplier.apply(target_file, patch) -> None`; `PatchApplier.rollback(target_file) -> None`; `apply_node(state, applier) -> {}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_apply.py
from pathlib import Path

from apply.applier import PatchApplier
from agent.nodes.apply import apply_node
from agent.state import ProposedFix
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
    fix = ProposedFix(
        description="cap growth",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    state = {"incident": make_incident(), "proposed_fix": fix}

    result = await apply_node(state, applier)

    assert result == {}
    assert applier.applied == [("mock_app/faults/memory_leak.py", "+cap = 100\n")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_apply.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apply'`

- [ ] **Step 3: Implement PatchApplier**

```python
# apply/__init__.py
(empty)

# apply/applier.py
"""Apply a proposed patch to the working tree with backup for rollback."""

from __future__ import annotations

import shutil
from pathlib import Path


class PatchApplier:
    def __init__(self, repo_root: Path, backup_dir: Path):
        self._root = repo_root
        self._backups = backup_dir

    def apply(self, *, target_file: str, patch: str) -> None:
        path = self._root / target_file
        self._backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, self._backups / Path(target_file).name)
        path.write_text(patch)

    def rollback(self, *, target_file: str) -> None:
        backup = self._backups / Path(target_file).name
        dest = self._root / target_file
        shutil.copy2(backup, dest)
```

- [ ] **Step 4: Implement apply_node**

```python
# agent/nodes/apply.py
"""Apply node: write the proven patch to the live working tree."""

from __future__ import annotations

from agent.state import AgentState


async def apply_node(state: AgentState, applier) -> dict:
    fix = state["proposed_fix"]
    applier.apply(target_file=fix.target_file, patch=fix.patch)
    return {}
```

- [ ] **Step 5: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_apply.py -v`
Expected: PASS

- [ ] **Step 6: Add `apply` to pyproject.toml packages, commit**

```bash
git add -A && git commit -m "feat(apply): PatchApplier with backup/rollback + apply_node"
```

---

### Task 5: HITL Gate + hitl_node

**Files:**
- Create: `hitl/__init__.py`
- Create: `hitl/gate.py`
- Create: `hitl/notifier.py`
- Create: `agent/nodes/hitl.py`
- Create: `tests/test_hitl_gate.py`

**Interfaces:**
- Consumes: `IncidentEvent`, `ProposedFix`, `PolicyVerdict` from state; `HitlDecision`, `HitlChoice` from `agent.state`
- Produces: `ApprovalGate.request_approval(incident, fix, verdict) -> HitlDecision`; `ApprovalGate.list_pending() -> list[dict]`; `ApprovalGate.resolve(incident_id, choice, note) -> None`; `hitl_node(state, gate) -> {"hitl_decision": HitlDecision}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hitl_gate.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hitl_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hitl'`

- [ ] **Step 3: Implement ApprovalGate**

```python
# hitl/__init__.py
(empty)

# hitl/gate.py
"""Async approval gate: suspends the graph until a human resolves."""

from __future__ import annotations

import asyncio

from agent.state import HitlChoice, HitlDecision


class ApprovalGate:
    def __init__(self, notifier=None):
        self._pending: dict[str, asyncio.Future] = {}
        self._items: dict[str, dict] = {}
        self._notifier = notifier

    async def request_approval(self, *, incident, fix, verdict) -> HitlDecision:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[HitlDecision] = loop.create_future()
        iid = incident.incident_id
        self._pending[iid] = fut
        self._items[iid] = {
            "incident_id": iid,
            "title": incident.title,
            "target_file": fix.target_file,
            "description": fix.description,
            "patch": fix.patch,
            "policy": verdict.model_dump(),
        }
        if self._notifier:
            self._notifier.notify(self._items[iid])
        return await fut

    def list_pending(self) -> list[dict]:
        return list(self._items.values())

    def resolve(self, incident_id: str, choice: HitlChoice, note: str | None = None) -> None:
        fut = self._pending.pop(incident_id, None)
        self._items.pop(incident_id, None)
        if fut and not fut.done():
            fut.set_result(HitlDecision(choice=choice, note=note))
```

- [ ] **Step 4: Implement Notifier protocol**

```python
# hitl/notifier.py
"""Notifier protocol for pluggable push notifications (Telegram, Slack, etc.)."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def notify(self, item: dict) -> None: ...
```

- [ ] **Step 5: Implement hitl_node**

```python
# agent/nodes/hitl.py
"""HITL node: suspend the graph until a human approves or rejects."""

from __future__ import annotations

from agent.state import AgentState


async def hitl_node(state: AgentState, gate) -> dict:
    decision = await gate.request_approval(
        incident=state["incident"],
        fix=state["proposed_fix"],
        verdict=state["policy_verdict"],
    )
    return {"hitl_decision": decision}
```

- [ ] **Step 6: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hitl_gate.py -v`
Expected: PASS

- [ ] **Step 7: Add `hitl` to pyproject.toml packages, commit**

```bash
git add -A && git commit -m "feat(hitl): ApprovalGate with asyncio Future + hitl_node"
```

---

### Task 6: Report Node

**Files:**
- Create: `agent/nodes/report.py`
- Create: `tests/test_report_node.py`

**Interfaces:**
- Consumes: full `AgentState` (reads outcome-determining fields, sums usage)
- Produces: `report_node(state, sink) -> {"report": IncidentReport}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_report_node.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_report_node.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.nodes.report'`

- [ ] **Step 3: Implement report_node**

```python
# agent/nodes/report.py
"""Report node: emit a final IncidentReport summarizing the run outcome."""

from __future__ import annotations

from agent.state import (
    AgentState,
    HitlChoice,
    IncidentReport,
    Outcome,
    PolicyDecision,
)


def _determine_outcome(state: AgentState) -> Outcome:
    hitl = state.get("hitl_decision")
    if hitl and hitl.choice == HitlChoice.approve:
        return Outcome.applied
    if hitl and hitl.choice == HitlChoice.reject:
        return Outcome.rejected
    verdict = state.get("policy_verdict")
    if verdict and verdict.decision == PolicyDecision.block:
        return Outcome.blocked
    return Outcome.escalated


async def report_node(state: AgentState, sink) -> dict:
    incident = state["incident"]
    triage = state.get("triage")
    sandbox = state.get("sandbox_result")
    verdict = state.get("policy_verdict")
    hitl = state.get("hitl_decision")
    fix = state.get("proposed_fix")

    total_in = sum(u.input_tokens for u in state.get("usage", []))
    total_out = sum(u.output_tokens for u in state.get("usage", []))
    total_lat = sum(u.latency_ms for u in state.get("usage", []))

    report = IncidentReport(
        incident_id=incident.incident_id,
        fault_kind=incident.fault_kind,
        outcome=_determine_outcome(state),
        triage_confidence=triage.confidence if triage else None,
        retries=state.get("retries", 0),
        sandbox_passed=sandbox.passed if sandbox else None,
        policy_decision=verdict.decision if verdict else None,
        hitl_choice=hitl.choice if hitl else None,
        applied_target_file=fix.target_file if fix and _determine_outcome(state) == Outcome.applied else None,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_latency_ms=total_lat,
    )
    sink.emit(report)
    return {"report": report}
```

- [ ] **Step 4: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_report_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(report): report_node emitting IncidentReport with outcome derivation"
```

---

### Task 7: Graph Wiring (Conditional Edges + Retry Loop)

**Files:**
- Modify: `agent/graph.py`
- Modify: `agent/nodes/propose.py` (add retry counter increment)
- Modify: `tests/test_agent_graph.py`

**Interfaces:**
- Consumes: all nodes from Tasks 1-6; `build_graph` signature expands
- Produces: `build_graph(triage_llm, propose_llm, search_tool, executor, policy_engine, approval_gate, applier, report_sink) -> CompiledStateGraph`; routing functions `route_after_sandbox`, `route_after_policy`, `route_after_hitl`

- [ ] **Step 1: Update propose_node to increment retries on re-entry**

```python
# agent/nodes/propose.py — change the return dict in propose_node
# Add retry increment: if sandbox already ran (re-entry), bump retries
async def propose_node(state: AgentState, llm) -> dict:
    structured = llm.with_structured_output(ProposedFix, include_raw=True)
    start = time.monotonic()
    response = await structured.ainvoke(_prompt(state))
    latency_ms = (time.monotonic() - start) * 1000

    usage_metadata = getattr(response["raw"], "usage_metadata", None) or {}
    usage = NodeUsage(
        node="propose",
        model=getattr(llm, "model", "unknown"),
        input_tokens=usage_metadata.get("input_tokens", 0),
        output_tokens=usage_metadata.get("output_tokens", 0),
        latency_ms=latency_ms,
    )
    retries = state["retries"] + (1 if state.get("sandbox_result") else 0)
    return {
        "proposed_fix": response["parsed"],
        "usage": state["usage"] + [usage],
        "retries": retries,
    }
```

- [ ] **Step 2: Rewrite graph.py with full Week 3 wiring**

```python
# agent/graph.py
"""LangGraph state machine: Triage -> Retrieve -> Propose -> Sandbox -> Policy -> HITL -> Apply -> Report."""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes.apply import apply_node
from agent.nodes.hitl import hitl_node
from agent.nodes.policy import policy_node
from agent.nodes.propose import propose_node
from agent.nodes.report import report_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.sandbox import sandbox_node
from agent.nodes.triage import triage_node
from agent.state import AgentState, HitlChoice, PolicyDecision

MAX_RETRIES = 2


def route_after_sandbox(state: AgentState) -> str:
    if state["sandbox_result"].passed:
        return "policy"
    if state["retries"] < MAX_RETRIES:
        return "propose"
    return "report"


def route_after_policy(state: AgentState) -> str:
    if state["policy_verdict"].decision == PolicyDecision.block:
        return "report"
    return "hitl"


def route_after_hitl(state: AgentState) -> str:
    if state["hitl_decision"].choice == HitlChoice.approve:
        return "apply"
    return "report"


def build_graph(
    triage_llm,
    propose_llm,
    search_tool,
    executor,
    policy_engine,
    approval_gate,
    applier,
    report_sink,
) -> CompiledStateGraph:
    g = StateGraph(AgentState)
    g.add_node("triage", partial(triage_node, llm=triage_llm))
    g.add_node("retrieve", partial(retrieve_node, search_tool=search_tool))
    g.add_node("propose", partial(propose_node, llm=propose_llm))
    g.add_node("sandbox", partial(sandbox_node, executor=executor))
    g.add_node("policy", partial(policy_node, engine=policy_engine))
    g.add_node("hitl", partial(hitl_node, gate=approval_gate))
    g.add_node("apply", partial(apply_node, applier=applier))
    g.add_node("report", partial(report_node, sink=report_sink))

    g.add_edge(START, "triage")
    g.add_edge("triage", "retrieve")
    g.add_edge("retrieve", "propose")
    g.add_edge("propose", "sandbox")

    g.add_conditional_edges("sandbox", route_after_sandbox, {
        "propose": "propose",
        "policy": "policy",
        "report": "report",
    })
    g.add_conditional_edges("policy", route_after_policy, {
        "hitl": "hitl",
        "report": "report",
    })
    g.add_conditional_edges("hitl", route_after_hitl, {
        "apply": "apply",
        "report": "report",
    })

    g.add_edge("apply", "report")
    g.add_edge("report", END)
    return g.compile()
```

- [ ] **Step 3: Rewrite test_agent_graph.py to cover all 4 paths**

```python
# tests/test_agent_graph.py
from datetime import datetime, timezone

from agent.graph import build_graph
from agent.state import (
    HitlChoice,
    HitlDecision,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    ProposedFix,
    SandboxResult,
    TriageResult,
)
from stream.schema import FaultKind, IncidentEvent, Severity
from tests.test_agent_nodes import FakeLLM, FakeSearchTool
from tests.test_sandbox_node import FakeExecutor
from tests.test_hitl_gate import FakeGate
from tests.test_report_node import FakeSink


class FakeApplier:
    def __init__(self):
        self.applied = []

    def apply(self, *, target_file, patch):
        self.applied.append((target_file, patch))


def make_incident() -> IncidentEvent:
    now = datetime.now(timezone.utc)
    return IncidentEvent(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        severity=Severity.critical,
        source="mock_app",
        title="memory_leak on mock_app",
        summary="rss climbed to 200MB",
        first_seen=now,
        last_seen=now,
        event_count=3,
        duplicate_count=5,
        sample_events=[],
        correlation_window_s=3.0,
    )


def _init_state():
    return {
        "incident": make_incident(),
        "triage": None,
        "retrieved_context": [],
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }


def _build(sandbox_result, policy_engine, hitl_decision, applier=None):
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="rss grows linearly",
    )
    proposed_fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    llm = FakeLLM({TriageResult: triage_result, ProposedFix: proposed_fix})
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "cap list", "score": 0.8}])
    executor = FakeExecutor(sandbox_result)
    gate = FakeGate(hitl_decision) if hitl_decision else FakeGate(HitlDecision(choice=HitlChoice.reject))
    sink = FakeSink()
    if applier is None:
        applier = FakeApplier()

    graph = build_graph(llm, llm, search_tool, executor, policy_engine, gate, applier, sink)
    return graph, sink, applier


async def test_happy_path_sandbox_pass_policy_allow_hitl_approve():
    from policy.engine import PolicyEngine
    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=100)
    decision = HitlDecision(choice=HitlChoice.approve)
    graph, sink, applier = _build(sandbox_ok, PolicyEngine(), decision)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.applied
    assert len(applier.applied) == 1


async def test_sandbox_fail_retries_then_escalates():
    from policy.engine import PolicyEngine
    sandbox_fail = SandboxResult(passed=False, exit_code=1, stdout="", stderr="fail", duration_ms=100)
    graph, sink, _ = _build(sandbox_fail, PolicyEngine(), None)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.escalated
    assert result["retries"] == 2


async def test_policy_block_skips_hitl():
    from policy.engine import PolicyEngine

    class BlockEngine:
        def evaluate(self, **kwargs):
            return PolicyVerdict(decision=PolicyDecision.block, violated_rules=["protected_path"])

    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=100)
    graph, sink, applier = _build(sandbox_ok, BlockEngine(), None)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.blocked
    assert result["hitl_decision"] is None
    assert len(applier.applied) == 0


async def test_hitl_reject_skips_apply():
    from policy.engine import PolicyEngine
    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=100)
    decision = HitlDecision(choice=HitlChoice.reject, note="looks risky")
    graph, sink, applier = _build(sandbox_ok, PolicyEngine(), decision)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.rejected
    assert len(applier.applied) == 0
```

- [ ] **Step 4: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp_retrieval_server.py --ignore=tests/test_retrieval_search.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(graph): full Week 3 wiring with conditional edges, retry loop, 4 terminal paths"
```

---

### Task 8: HITL Web UI

**Files:**
- Create: `hitl/web.py`
- Create: `tests/test_hitl_web.py`

**Interfaces:**
- Consumes: `ApprovalGate` instance (shared with running agent)
- Produces: `create_hitl_app(gate) -> FastAPI`; `GET /` lists pending; `POST /decision/{incident_id}` resolves gate

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hitl_web.py
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

    resp = client.post(f"/decision/{incident.incident_id}", data={"choice": "approve", "note": ""})
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hitl_web.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_hitl_app'`

- [ ] **Step 3: Implement HITL web app**

```python
# hitl/web.py
"""Minimal FastAPI approval UI for human-in-the-loop decisions."""

from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from agent.state import HitlChoice
from hitl.gate import ApprovalGate


def create_hitl_app(gate: ApprovalGate) -> FastAPI:
    app = FastAPI(title="Aegis HITL")

    @app.get("/", response_class=HTMLResponse)
    async def list_pending():
        items = gate.list_pending()
        if not items:
            return "<html><body><h2>No pending approvals</h2></body></html>"

        cards = ""
        for item in items:
            cards += f"""
            <div style="border:1px solid #ccc;padding:16px;margin:12px 0;border-radius:8px;">
                <h3>{item['title']}</h3>
                <p><b>Incident:</b> {item['incident_id']}</p>
                <p><b>Target:</b> {item['target_file']}</p>
                <p><b>Description:</b> {item['description']}</p>
                <pre style="background:#f4f4f4;padding:8px;overflow-x:auto;">{item['patch']}</pre>
                <form method="post" action="/decision/{item['incident_id']}" style="display:inline;">
                    <input type="hidden" name="choice" value="approve">
                    <input type="hidden" name="note" value="">
                    <button type="submit" style="background:green;color:white;padding:8px 16px;border:none;border-radius:4px;cursor:pointer;">Approve</button>
                </form>
                <form method="post" action="/decision/{item['incident_id']}" style="display:inline;margin-left:8px;">
                    <input type="hidden" name="choice" value="reject">
                    <input type="hidden" name="note" value="">
                    <button type="submit" style="background:red;color:white;padding:8px 16px;border:none;border-radius:4px;cursor:pointer;">Reject</button>
                </form>
            </div>
            """
        return f"<html><body><h2>Pending Approvals</h2>{cards}</body></html>"

    @app.post("/decision/{incident_id}")
    async def post_decision(incident_id: str, choice: str = Form(...), note: str = Form("")):
        hitl_choice = HitlChoice(choice)
        gate.resolve(incident_id, hitl_choice, note=note or None)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/pending")
    async def api_pending():
        return gate.list_pending()

    return app
```

- [ ] **Step 4: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hitl_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(hitl): local web UI with approve/reject buttons"
```

---

### Task 9: Integration (agent/main.py + docker-compose)

**Files:**
- Modify: `agent/main.py`
- Modify: `docker-compose.yml`
- Modify: `pyproject.toml` (packages list)
- Modify: `.env.example`

**Interfaces:**
- Consumes: all components from Tasks 1-8
- Produces: runnable `python -m agent.main` that starts web UI + processes incidents through full graph

- [ ] **Step 1: Update pyproject.toml**

Add `"docker>=7.1"` to dependencies. Add `"sandbox"`, `"policy"`, `"hitl"`, `"apply"` to `[tool.setuptools].packages`.

- [ ] **Step 2: Rewrite agent/main.py for full Week 3 integration**

```python
# agent/main.py
"""Demo entrypoint: run the full agent graph with HITL web UI."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from langchain_mcp_adapters.client import MultiServerMCPClient
from redis.asyncio import Redis

from agent.graph import build_graph
from agent.llm import get_propose_llm, get_triage_llm
from agent.state import AgentState, IncidentReport
from apply.applier import PatchApplier
from hitl.gate import ApprovalGate
from hitl.web import create_hitl_app
from policy.engine import PolicyEngine
from sandbox.executor import SandboxExecutor
from stream.consumer import INCIDENTS_STREAM
from stream.schema import IncidentEvent

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REPO_ROOT = Path(__file__).resolve().parents[1]
HITL_PORT = int(os.environ.get("HITL_PORT", "8001"))


class PrintSink:
    def emit(self, report: IncidentReport) -> None:
        print(f"\n{'='*70}")
        print(f"REPORT: {report.incident_id} -> {report.outcome.value}")
        print(f"  retries={report.retries} sandbox_passed={report.sandbox_passed}")
        print(f"  policy={report.policy_decision} hitl={report.hitl_choice}")
        print(f"  tokens: in={report.total_input_tokens} out={report.total_output_tokens}")
        print(f"  latency: {report.total_latency_ms:.0f}ms")
        if report.applied_target_file:
            print(f"  applied to: {report.applied_target_file}")
        print("="*70)


async def _read_latest_incidents(redis: Redis, count: int) -> list[IncidentEvent]:
    entries = await redis.xrevrange(INCIDENTS_STREAM, count=count)
    return [IncidentEvent.model_validate_json(fields["incident"]) for _, fields in entries]


async def main(count: int = 1) -> None:
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        incidents = await _read_latest_incidents(redis, count)
    finally:
        await redis.aclose()

    if not incidents:
        print("No incidents found on aegis:incidents.")
        return

    # Build dependencies
    import docker
    docker_client = docker.from_env()

    client = MultiServerMCPClient(
        {
            "retrieval": {
                "command": sys.executable,
                "args": ["-m", "tools.retrieval_server"],
                "transport": "stdio",
                "cwd": str(REPO_ROOT),
            }
        }
    )
    tools = await client.get_tools()
    search_tool = next(t for t in tools if t.name == "search_knowledge")

    gate = ApprovalGate()
    executor = SandboxExecutor(docker_client)
    policy_engine = PolicyEngine()
    applier = PatchApplier(repo_root=REPO_ROOT, backup_dir=REPO_ROOT / ".aegis_backups")
    sink = PrintSink()

    graph = build_graph(
        get_triage_llm(), get_propose_llm(), search_tool,
        executor, policy_engine, gate, applier, sink,
    )

    # Start HITL web UI in background
    hitl_app = create_hitl_app(gate)
    config = uvicorn.Config(hitl_app, host="0.0.0.0", port=HITL_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    print(f"HITL approval UI running at http://localhost:{HITL_PORT}")

    try:
        for incident in incidents:
            print(f"\nProcessing: {incident.title} ({incident.incident_id})")
            result = await graph.ainvoke(
                {
                    "incident": incident,
                    "triage": None,
                    "retrieved_context": [],
                    "proposed_fix": None,
                    "retries": 0,
                    "sandbox_result": None,
                    "policy_verdict": None,
                    "hitl_decision": None,
                    "report": None,
                    "usage": [],
                }
            )
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count))
```

- [ ] **Step 3: Update docker-compose.yml**

Add Docker socket mount to agent service, expose HITL port, add sandbox build:

```yaml
  agent:
    build: .
    command: python -m agent.main
    environment:
      REDIS_URL: redis://redis:6379/0
      POSTGRES_HOST: postgres
      POSTGRES_USER: ${POSTGRES_USER:-aegis}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-aegis}
      POSTGRES_DB: ${POSTGRES_DB:-aegis}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      TRIAGE_MODEL: ${TRIAGE_MODEL:-claude-haiku-4-5-20251001}
      PROPOSE_MODEL: ${PROPOSE_MODEL:-claude-sonnet-4-6}
      HITL_PORT: "8001"
    ports:
      - "8001:8001"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    profiles: ["tools"]
```

- [ ] **Step 4: Update .env.example**

Add:
```
# HITL approval UI
HITL_PORT=8001
```

- [ ] **Step 5: Run full test suite (excluding Postgres-dependent)**

Run: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp_retrieval_server.py --ignore=tests/test_retrieval_search.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: Week 3 integration — full graph with sandbox, policy, HITL, apply, report"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - Docker sandbox executor: Task 3 ✓
   - Policy engine: Task 2 ✓
   - HITL approval: Tasks 5, 8 ✓
   - Apply/rollback: Task 4 ✓
   - Graph wiring with retry loop: Task 7 ✓
   - Report/metrics emission: Task 6 ✓
   - Demo: a fix tested in isolation, blocked or approved, then applied/rolled back: Task 9 ✓

2. **Placeholder scan:** No TBD/TODO/placeholders found.

3. **Type consistency:**
   - `SandboxResult`, `PolicyVerdict`, `HitlDecision` — consistent names/fields across all tasks
   - `build_graph` signature: 8 params in Task 7 = same 8 used in Task 9
   - `FakeExecutor`, `FakeGate`, `FakeSink`, `FakeApplier` — consistent interfaces across test files
   - `route_after_sandbox/policy/hitl` return strings matching node names in `add_conditional_edges`
   - `_determine_outcome` logic in report_node covers all 4 `Outcome` variants
