# 🔍 Root Cause Analyser — Teams Bot

AI-powered Root Cause Analysis bot for the Solargraf platform. Analyzes error reports by searching SigNoz logs and the codebase (solargraf-api, graf-apps, design-tool) using semantic search, then uses Gemini to identify the root cause.

## Architecture

```
Teams User → MS Teams → Bot Framework (Python) → FastAPI Server
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    ▼                 ▼                 ▼
                              SigNoz Mock       ChromaDB           Gemini API
                             (log fetcher)    (code search)      (LLM analysis)
```

## Prerequisites

- Python 3.11+
- Docker (for ChromaDB)
- Gemini API key ([Get one free](https://aistudio.google.com/app/apikey))
- ngrok (for local Teams bot development)

## Quick Start

### 1. Setup ChromaDB

```bash
# Option A: Docker Compose (recommended)
cd root-cause-analyser
docker-compose up -d

# Option B: Direct docker run
docker run -d --name rca-chromadb -p 8000:8000 \
  -e IS_PERSISTENT=TRUE \
  -e ANONYMIZED_TELEMETRY=FALSE \
  chromadb/chroma:latest
```

Verify: `curl http://localhost:8000/api/v1/heartbeat`

### 2. Install Python Dependencies

```bash
cd root-cause-analyser
python -m venv venv
source venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env and set:
#   GEMINI_API_KEY=your-key-here
#   MICROSOFT_APP_ID=your-bot-app-id        (optional for local testing)
#   MICROSOFT_APP_PASSWORD=your-bot-secret   (optional for local testing)
```

### 4. Index the Codebase

```bash
# Index all 3 repos
python -m indexer.index_codebase

# Or index a specific repo
python -m indexer.index_codebase --repo solargraf-api

# Reset and re-index
python -m indexer.index_codebase --reset
```

This walks `solargraf-api`, `graf-apps`, and `design-tool`, chunks `.js/.ts/.jsx/.tsx` files, and stores them in ChromaDB for semantic search.

### 5. Start the Bot Server

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 3978 --reload
```

Health check: `curl http://localhost:3978/`

### 6. Expose via ngrok (for Teams)

```bash
ngrok http 3978
```

Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.app`).

### 7. Register the Bot in Teams

1. Go to [Azure Bot Service](https://portal.azure.com/#create/Microsoft.AzureBot) or [Teams Developer Portal](https://dev.teams.microsoft.com/)
2. Create a new bot registration
3. Set the **Messaging endpoint** to: `https://your-ngrok-url/api/messages`
4. Copy the **App ID** and **Password** into your `.env` file
5. Install the bot in Teams

## Project Structure

```
root-cause-analyser/
├── main.py                    # FastAPI server + Bot Framework entrypoint
├── bot.py                     # Teams bot (ActivityHandler)
├── config.py                  # Environment configuration
├── requirements.txt           # Python dependencies
├── docker-compose.yml         # ChromaDB container
├── .env.example               # Environment template
├── agents/
│   ├── rca_agent.py           # Orchestrator: logs + code search + LLM
│   └── prompts.py             # System/user prompt templates
├── services/
│   ├── signoz_client.py       # Mock SigNoz log fetcher
│   ├── code_search.py         # ChromaDB query interface
│   └── gemini_client.py       # Gemini API wrapper
├── indexer/
│   └── index_codebase.py      # Codebase → ChromaDB indexer
└── mock_data/
    └── signoz_logs.json       # Sample log entries for demos
```

## Demo Scenarios

The mock SigNoz data includes these pre-built scenarios:

| Error Query | Root Cause |
|-------------|-----------|
| "Download DWG from SDT/EDT is failing" | Missing `await` in `libs/greenthink/index.js:getSiteplan` |
| "Auto-design timeout during panel placement" | Infinite loop in design-tool panel placer |
| "3D rendering crash" | Null geometry object in `libs/drawing3d/` |
| "Financial calculation returning NaN" | Division by zero from empty Genability response |
| "Screenshot generation failing" | Missing Chromium browser bundle |

## Swapping Mock SigNoz for Real

Replace the `SigNozClient.fetch_logs()` method in `services/signoz_client.py` with actual HTTP calls:

```python
import aiohttp

async def fetch_logs(self, query: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{self.api_url}/api/v3/query_range",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "limit": 50},
        ) as resp:
            data = await resp.json()
            return data.get("result", [])
```
