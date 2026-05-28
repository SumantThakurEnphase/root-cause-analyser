# 🔍 Root Cause Analyser — Teams Bot

AI-powered Root Cause Analysis bot for the Solargraf platform. Analyzes error reports by searching SigNoz logs and the codebase (solargraf-api, graf-apps, design-tool) using semantic search, then uses Gemini to identify the root cause.

## Architecture

```
Teams User → MS Teams → Bot Framework (Python) → FastAPI Server
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    ▼                 ▼                 ▼
                              SigNoz API       ChromaDB           Gemini API
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
#   SIGNOZ_API_KEY=your-signoz-bearer-token
#   SIGNOZ_API_URL=https://monitoring-develop.solargraf.com/api/v5/query_range  (default)
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
│   ├── signoz_client.py       # SigNoz log fetcher (live API)
│   ├── input_parser.py        # URL parsing + AnalysisRequest
│   ├── api_discovery.py       # API endpoint discovery via codebase search
│   ├── code_search.py         # ChromaDB query interface
│   └── gemini_client.py       # Gemini API wrapper
├── indexer/
│   └── index_codebase.py      # Codebase → ChromaDB indexer
```

## Usage

### With project URL (project-aware pipeline)

```bash
curl -X POST http://localhost:3978/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://app.solargraf.com/projects/342321", "query": "roofline detection not working"}'
```

### Without URL (query-only)

```bash
curl -X POST http://localhost:3978/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"query": "Download DWG from SDT/EDT is failing"}'
```
