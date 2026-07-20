"""FastAPI HITL UI: evidence-brief approval cards + durable-fix promote queue (ADR-0005)."""

from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from agent.state import HitlChoice
from hitl.gate import ApprovalGate
from hitl.registry import DurableFixRegistry

_DECIDABLE = {HitlChoice.approve.value, HitlChoice.reject.value}


def _esc(value) -> str:
    """HTML-escape any LLM/incident-derived value before interpolation."""
    return html.escape(value if isinstance(value, str) else str(value))


def _url_id(incident_id) -> str:
    """URL-encode an incident id for a route path. A promoted run's id is
    '<id>#promo'; the '#' is a URL fragment delimiter, so an un-encoded form
    action drops it and the POST hits the wrong (base) id -> 404."""
    return quote(str(incident_id), safe="")


def _evidence_table(evidence: dict | None) -> str:
    if evidence is None:
        return "<p><i>No sandbox evidence (non-reproducible fault or unattempted).</i></p>"
    if not evidence["passed"]:
        return f"<p><b>Sandbox failed:</b> {_esc(evidence['failure_reason'])}</p>"
    before = evidence["before_metrics"] or {}
    after = evidence["after_metrics"] or {}
    rows = ""
    for metric in sorted(set(before) | set(after)):
        rows += (f"<tr><td>{_esc(metric)}</td><td>{_esc(before.get(metric, '-'))}</td>"
                 f"<td>{_esc(after.get(metric, '-'))}</td></tr>")
    return f"""
    <table border="1" cellpadding="4" style="border-collapse:collapse;">
        <tr><th>metric</th><th>before</th><th>after</th></tr>
        {rows}
    </table>
    """


def _policy_block(policy: dict) -> str:
    rules = "".join(
        f"<li>{_esc(rule)}: {_esc(reason)}</li>"
        for rule, reason in zip(policy.get("violated_rules", []), policy.get("reasons", []))
    )
    return f"""
    <p><b>Policy decision:</b> {_esc(policy['decision'])}</p>
    <ul>{rules}</ul>
    """


def _durable_fix_card(fix: dict | None, incident_id: str) -> str:
    if fix is None:
        return ""
    return f"""
    <div style="margin-top:8px;padding:8px;background:#fafafa;">
        <b>Durable fix:</b> {_esc(fix['reason'])}<br/>
        <b>Target file:</b> {_esc(fix.get('target_file'))}
        <form method="post" action="/promote/{_url_id(incident_id)}" style="display:inline;margin-left:8px;">
            <button type="submit">Promote</button>
        </form>
    </div>
    """


def _brief_card(brief: dict) -> str:
    triage = brief.get("triage") or {}
    mitigation = brief["mitigation"]
    iid = _esc(brief["incident_id"])
    url_iid = _url_id(brief["incident_id"])
    return f"""
    <div style="border:1px solid #ccc;padding:16px;margin:12px 0;border-radius:8px;">
        <h3>{_esc(brief['title'])} <small>({iid})</small></h3>
        <p><b>Root cause:</b> {_esc(triage.get('root_cause', 'n/a'))}
           (confidence: {_esc(triage.get('confidence', 'n/a'))})</p>
        <p><b>Mitigation:</b> {_esc(mitigation['action'])} &mdash; {_esc(mitigation['reason'])}</p>
        {_evidence_table(brief['evidence'])}
        {_policy_block(brief['policy'])}
        {_durable_fix_card(brief['durable_fix'], brief['incident_id'])}
        <p><i>Expires at:</i> {_esc(brief['expires_at'])}</p>
        <form method="post" action="/decision/{url_iid}" style="display:inline;">
            <input type="hidden" name="choice" value="approve">
            <button type="submit" style="background:green;color:white;">Approve</button>
        </form>
        <form method="post" action="/decision/{url_iid}" style="display:inline;margin-left:8px;">
            <input type="hidden" name="choice" value="reject">
            <button type="submit" style="background:red;color:white;">Reject</button>
        </form>
    </div>
    """


def _durable_fix_row(entry: dict) -> str:
    return f"""
    <div style="border:1px solid #ddd;padding:8px;margin:8px 0;">
        <b>{_esc(entry['title'])}</b> ({_esc(entry['incident_id'])}) &mdash;
        {_esc(entry['action'])}: {_esc(entry['description'])}
        <form method="post" action="/promote/{_url_id(entry['incident_id'])}" style="display:inline;margin-left:8px;">
            <button type="submit">Promote</button>
        </form>
    </div>
    """


def create_hitl_app(gate: ApprovalGate, registry: DurableFixRegistry) -> FastAPI:
    app = FastAPI(title="Aegis HITL")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        briefs = gate.list_pending()
        cards = "".join(_brief_card(b) for b in briefs) or "<p>No pending approvals</p>"

        open_fixes = registry.list_open()
        fixes_html = "".join(_durable_fix_row(e) for e in open_fixes) or "<p>No open durable fixes</p>"

        return f"""
        <html><body>
            <h2>Pending Approvals</h2>
            {cards}
            <h2>Open durable fixes</h2>
            {fixes_html}
        </body></html>
        """

    @app.post("/decision/{incident_id}")
    async def post_decision(incident_id: str, choice: str = Form(...), note: str = Form("")):
        if choice not in _DECIDABLE:
            raise HTTPException(status_code=400, detail="choice must be approve or reject")
        if gate.get_context(incident_id) is None:
            raise HTTPException(status_code=404, detail="no pending approval for this incident")
        gate.resolve(incident_id, HitlChoice(choice), note=note or None)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/promote/{incident_id}")
    async def post_promote(incident_id: str):
        if registry.promote(incident_id):
            return RedirectResponse(url="/", status_code=303)

        context = gate.get_context(incident_id)
        if context is None or context.get("durable_fix") is None:
            raise HTTPException(status_code=404, detail="no durable fix to promote")

        registry.file(incident=context["incident"], triage=context["triage"],
                      fix=context["durable_fix"])
        registry.promote(incident_id)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/pending")
    async def api_pending():
        return gate.list_pending()

    @app.get("/api/durable-fixes")
    async def api_durable_fixes():
        return registry.list_open()

    return app
