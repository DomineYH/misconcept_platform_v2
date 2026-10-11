"""Existing result assertions now wait through the durable HTTP reservation."""

import asyncio
import json
from uuid import uuid4


async def settled_analysis_request(client, url, **kwargs):
    content = kwargs.pop("content", None)
    body = {
        **kwargs.pop("json", json.loads(content) if content else {}),
        "request_id": str(uuid4()),
    }
    response = await client.post(url, json=body, **kwargs)
    if response.status_code != 202:
        return response
    assert response.json()["run_id"]
    status_url = response.json()["actions"]["status"]
    async with asyncio.timeout(10):
        while True:
            state = await client.get(status_url)
            assert state.status_code == 200, state.text
            if state.json()["latest_run"]["status"] != "running":
                break
            await asyncio.sleep(0.02)
    prefix = "/admin" if url.startswith("/admin") else ""
    session_id = (
        response.json()["session_id"]
        if "session_id" in response.json()
        else url.split("/sessions/")[1].split("/")[0]
    )
    return await client.get(f"{prefix}/sessions/{session_id}/analysis")


class AnalysisApi:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def post(self, url, **kwargs):
        if url.endswith(("/analyze", "/analyze_regenerate")) and not kwargs.get(
            "json", {}
        ).get("request_id"):
            return await settled_analysis_request(self.client, url, **kwargs)
        return await self.client.post(url, **kwargs)


async def call_analysis_route(handler, request, session_id, user, db):
    """Keep preexisting service result assertions after async reservation."""
    import json

    from src.api.schemas import AnalysisRequest
    from src.services.analysis_results import load_analysis_response
    from src.services.analysis_runs import active_analyses

    response = await handler(
        request, AnalysisRequest(request_id=str(uuid4())), session_id, user, db
    )
    initial = json.loads(response.body)
    if response.status_code == 202:
        task = active_analyses.get(initial["run_id"])
        if task is not None:
            await task
    return await load_analysis_response(session_id, db, admin=user.is_admin)
