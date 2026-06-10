"""
FastAPI entrypoint for the Root Cause Analyser Teams Bot.

Serves the Bot Framework messaging endpoint and a health check.
Run with: uvicorn main:app --host 0.0.0.0 --port 3978 --reload
"""

import asyncio
import os
import sys
import time
import traceback
import uuid
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\.")
warnings.filterwarnings("ignore", module=r"urllib3\.", message=r".*NotOpenSSLWarning.*|.*OpenSSL.*")

from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity

from config import config
from bot import RCABot
from agents.rca_agent import RCAAgent
from services.analysis_store import AnalysisStore


class RCARequest(BaseModel):
    query: str
    url: Optional[str] = ""


rca_agent = RCAAgent()
analysis_store = AnalysisStore()

# Bot Framework adapter
adapter_settings = BotFrameworkAdapterSettings(
    app_id=config.MICROSOFT_APP_ID,
    app_password=config.MICROSOFT_APP_PASSWORD,
)
adapter = BotFrameworkAdapter(adapter_settings)


# Error handler
async def on_error(context: TurnContext, error: Exception):
    print(f"\n [on_turn_error] unhandled error: {error}", file=sys.stderr)
    traceback.print_exc()
    await context.send_activity("⚠️ The bot encountered an error. Please try again.")


adapter.on_turn_error = on_error

# Create the bot
bot = RCABot()

# FastAPI app
app = FastAPI(
    title="Root Cause Analyser Bot",
    description="AI-powered RCA bot for Solargraf platform — Microsoft Teams integration",
    version="1.0.0",
)


@app.get("/")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "root-cause-analyser", "version": "1.0.0"}


@app.post("/api/messages")
async def messages(request: Request) -> Response:
    """
    Main endpoint for Bot Framework messages from Teams.
    The Bot Framework Connector sends all activities here.
    """
    if "application/json" not in (request.headers.get("Content-Type", "")):
        return Response(status_code=415)

    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    response = await adapter.process_activity(activity, auth_header, bot.on_turn)

    if response:
        return Response(
            content=response.body,
            status_code=response.status,
            headers=response.headers,
        )
    return Response(status_code=201)


def _run_analysis_sync(analysis_id: str, query: str, url: str):
    """
    Sync wrapper that runs the RCA pipeline in a background thread.
    Creates its own event loop so blocking I/O doesn't stall the main loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        start_time = time.time()
        result = loop.run_until_complete(rca_agent.analyze(query, url=url))

        # Write the analysis output to error_analysis.md
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_analysis.md")
        with open(output_path, "w") as f:
            f.write(result)
        print(f"[RCA] Analysis written to {output_path}")
        print(f"[RCA] Time taken: {time.time() - start_time:.1f}s")

        analysis_store.update(analysis_id, status="completed", analysis=result)
    except Exception as e:
        traceback.print_exc()
        analysis_store.update(analysis_id, status="failed", error=str(e))
    finally:
        loop.close()


@app.post("/api/analyze")
async def analyze(request: RCARequest):
    """
    Async RCA endpoint compatible with Power Automate's polling pattern.

    Returns 202 Accepted with a Location header. Power Automate (or any client)
    polls the Location URL until it returns 200 with the final result.

    Usage:
        curl -X POST http://localhost:3978/api/analyze \
          -H "Content-Type: application/json" \
          -d '{"query": "Download DWG from SDT/EDT is failing"}'

        # Response: 202 Accepted, Location: /api/analyze/status/<uuid>
        # Poll:    GET /api/analyze/status/<uuid>
    """
    analysis_id = str(uuid.uuid4())
    analysis_store.create(analysis_id, query=request.query, url=request.url or "")

    # Fire off analysis in a background thread
    asyncio.get_event_loop().run_in_executor(
        None, _run_analysis_sync, analysis_id, request.query, request.url or ""
    )

    status_url = f"/api/analyze/status/{analysis_id}"
    return Response(
        status_code=202,
        headers={"Location": f"https://hockey-beta-aaron-lexmark.trycloudflare.com{status_url}", "Retry-After": "15"},
        content=None,
    )


@app.get("/api/analyze/status/{analysis_id}")
async def get_analysis_status(analysis_id: str):
    """
    Polling endpoint for async analysis results.

    Power Automate calls this automatically based on the Location header.
    - 202 + Location header → still running, keep polling
    - 200 + JSON body       → done (completed or failed), stop polling
    - 404                   → unknown analysis ID
    """
    entry = analysis_store.get(analysis_id)
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "Analysis not found"})

    if entry["status"] == "in_progress":
        status_url = f"/api/analyze/status/{analysis_id}"
        return Response(
            status_code=202,
            headers={"Location": f"https://hockey-beta-aaron-lexmark.trycloudflare.com{status_url}", "Retry-After": "15"},
            content=None,
        )

    # Terminal state — completed or failed
    return JSONResponse(
        status_code=200,
        content={
            "query": entry["query"],
            "url": entry["url"],
            "analysis": entry.get("analysis"),
            "error": entry.get("error"),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3978,
        reload=True,
    )
