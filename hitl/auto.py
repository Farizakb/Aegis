"""Auto-approver stub: eval runs measure the apply path unattended (ADR-0005)."""

from __future__ import annotations

from agent.state import HitlChoice, HitlDecision


class AutoApprover:
    async def request_approval(self, **_kwargs) -> HitlDecision:
        return HitlDecision(choice=HitlChoice.approve, decided_by="auto-approver")
