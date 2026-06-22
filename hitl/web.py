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
