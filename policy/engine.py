"""Deterministic, deny-by-default, stateful policy engine (ADR-0004).

Seven ordered rules over the typed action catalog, sandbox evidence, triage
confidence, and action history. No LLM, no wall clock inside rules (clock is
injected), no randomness — every verdict is reproducible and auditable.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable

from pydantic import ValidationError

from agent.actions import CATALOG, ActionType, ProposedAction
from agent.state import PolicyDecision, PolicyVerdict, SandboxResult, TriageResult

CONFIDENCE_FLOOR = 0.7
FLAP_WINDOW_S = 600.0
FLAP_MAX_APPLIES = 2
RATE_WINDOW_S = 3600.0
RATE_MAX_AUTO_APPLIES = 5
ALLOWED_PATH_PREFIXES = ("mock_app/",)
PROTECTED_PATHS = (
    "docker-compose.yml", "policy/", "agent/", ".env",
    "stream/", "retrieval/", "sandbox/", "hitl/", "apply/",
)
MAX_PATCH_LINES = 80

_SEVERITY = {PolicyDecision.allow: 0, PolicyDecision.needs_approval: 1, PolicyDecision.block: 2}


class PolicyEngine:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._applies: deque[tuple[str, str, float]] = deque()  # (action, target, ts)
        self._auto_applies: deque[float] = deque()

    def evaluate(
        self,
        *,
        proposal: ProposedAction | dict[str, Any],
        sandbox: SandboxResult | None,
        triage: TriageResult | None,
    ) -> PolicyVerdict:
        # Rule 1 — deny-by-default: anything that does not parse into the
        # typed catalog is blocked. The only rule that short-circuits.
        if not isinstance(proposal, ProposedAction):
            try:
                proposal = ProposedAction.model_validate(proposal)
            except ValidationError as exc:
                return PolicyVerdict(
                    decision=PolicyDecision.block,
                    violated_rules=["deny_by_default"],
                    reasons=[f"proposal does not parse into the action catalog: {exc.errors()[0]['msg']}"],
                )

        # Escalate is the safe terminal hand-off to a human: it executes
        # nothing, so no further rule applies. Deliberate, not an oversight.
        if proposal.action is ActionType.escalate:
            return PolicyVerdict(decision=PolicyDecision.allow, violated_rules=[], reasons=[])

        meta = CATALOG[proposal.action]
        violations: list[tuple[str, str, PolicyDecision]] = []

        # Rule 2 — sandbox proof required
        if sandbox is None or not sandbox.passed:
            detail = "no sandbox verification was run" if sandbox is None else "sandbox verification failed"
            violations.append(("sandbox_proof_required", detail, PolicyDecision.needs_approval))

        # Rule 3 — irreversibility gate
        if not meta.reversible or meta.requires_approval:
            violations.append((
                "irreversibility_gate",
                f"{proposal.action.value} is irreversible or catalog-flagged for approval",
                PolicyDecision.needs_approval,
            ))

        # Rule 4 — confidence floor
        confidence = triage.confidence if triage is not None else 0.0
        if confidence < CONFIDENCE_FLOOR:
            violations.append((
                "confidence_floor",
                f"triage confidence {confidence:.2f} below floor {CONFIDENCE_FLOOR}",
                PolicyDecision.needs_approval,
            ))

        # Rule 5 — flap protection (stateful; Task 3)
        violations.extend(self._flap_violations(proposal))

        # Rule 6 — rate limit circuit (stateful; Task 3)
        violations.extend(self._rate_violations())

        # Rule 7 — patch rules
        if proposal.action is ActionType.patch_code:
            violations.extend(self._patch_violations(proposal))

        if not violations:
            return PolicyVerdict(decision=PolicyDecision.allow, violated_rules=[], reasons=[])
        decision = max((v[2] for v in violations), key=_SEVERITY.__getitem__)
        return PolicyVerdict(
            decision=decision,
            violated_rules=[v[0] for v in violations],
            reasons=[f"{v[0]}: {v[1]}" for v in violations],
        )

    def _patch_violations(self, proposal: ProposedAction) -> list[tuple[str, str, PolicyDecision]]:
        out: list[tuple[str, str, PolicyDecision]] = []
        tgt = proposal.target_file or ""
        if any(tgt.startswith(p) for p in PROTECTED_PATHS):
            out.append(("patch_protected_path", f"{tgt!r} is a protected control-plane path", PolicyDecision.block))
        elif not any(tgt.startswith(p) for p in ALLOWED_PATH_PREFIXES):
            out.append(("patch_path_allowlist", f"{tgt!r} outside allowlisted paths", PolicyDecision.needs_approval))
        if (proposal.patch or "").count("\n") > MAX_PATCH_LINES:
            out.append(("patch_size_cap", f"patch exceeds {MAX_PATCH_LINES} lines", PolicyDecision.needs_approval))
        return out

    # Stateful rules — implemented in Task 3; inert stubs keep Task 2 green.
    def _flap_violations(self, proposal: ProposedAction) -> list[tuple[str, str, PolicyDecision]]:
        return []

    def _rate_violations(self) -> list[tuple[str, str, PolicyDecision]]:
        return []

    def record_apply(self, proposal: ProposedAction, decision: PolicyDecision) -> None:
        return None
