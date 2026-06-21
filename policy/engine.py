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
