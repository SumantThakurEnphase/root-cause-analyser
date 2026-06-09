"""
FastAPI entrypoint for the Root Cause Analyser Teams Bot.

Serves the Bot Framework messaging endpoint and a health check.
Run with: uvicorn main:app --host 0.0.0.0 --port 3978 --reload
"""

import os
import sys
import traceback
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\.")
warnings.filterwarnings("ignore", module=r"urllib3\.", message=r".*NotOpenSSLWarning.*|.*OpenSSL.*")

from typing import Optional

from fastapi import FastAPI, Request, Response
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


class RCARequest(BaseModel):
    query: str
    url: Optional[str] = ""


rca_agent = RCAAgent()

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


@app.post("/api/analyze")
async def analyze(request: RCARequest):
    """
    Direct REST API for testing RCA without Teams.

    Usage (with URL — project-aware):
        curl -X POST http://localhost:3978/api/analyze \
          -H "Content-Type: application/json" \
          -d '{"url": "https://app.solargraf.com/projects/342321", "query": "roofline detection not working"}'

    Usage (without URL — query-only, backward compatible):
        curl -X POST http://localhost:3978/api/analyze \
          -H "Content-Type: application/json" \
          -d '{"query": "Download DWG from SDT/EDT is failing"}'
    """
    try:
        result = await rca_agent.analyze(request.query, url=request.url or "")

        # Write the analysis output to analysis.md
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prod_503.md")
        with open(output_path, "w") as f:
            f.write(result)
        print(f"[RCA] Analysis written to {output_path}")

        return {"query": request.query, "url": request.url, "analysis": result}
    except Exception as e:
        traceback.print_exc()
        return {"query": request.query, "url": request.url, "error": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3978,
        reload=True,
    )
