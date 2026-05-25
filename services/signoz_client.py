"""
SigNoz client — fetches logs from the SigNoz Query API (v5/query_range).

Falls back to mock data if the API is unreachable or not configured.
"""

import json
import os
import time
import requests
from typing import Any

_MOCK_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mock_data",
    "signoz_logs.json",
)

# Default look-back window: 10 days in milliseconds
_DEFAULT_LOOKBACK_MS = 10 * 24 * 60 * 60 * 1000


class SigNozClient:
    def __init__(self, api_url: str = "", api_key: str = "", use_mock: bool = False):
        self.api_url = api_url
        self.api_key = api_key
        self.use_mock = use_mock
        self._scenarios: list[dict] = []
        self._load_mock_data()

    def _load_mock_data(self) -> None:
        try:
            with open(_MOCK_DATA_PATH, "r") as f:
                data = json.load(f)
            self._scenarios = data.get("scenarios", [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load mock SigNoz data: {e}")
            self._scenarios = []

    def _build_signoz_payload(self, expression: str, lookback_ms: int = _DEFAULT_LOOKBACK_MS, limit: int = 20) -> dict:
        """Build a SigNoz v5/query_range payload matching the JS issueAnalyzer format."""
        now = int(time.time() * 1000)
        return {
            "schemaVersion": "v1",
            "start": now - lookback_ms,
            "end": now,
            "requestType": "raw",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "logs",
                            "filter": {
                                "expression": expression,
                            },
                            "limit": limit,
                            "order": [
                                {
                                    "key": {"name": "timestamp"},
                                    "direction": "desc",
                                }
                            ],
                        },
                    }
                ]
            },
            "formatOptions": {
                "formatTableResultForUI": False,
                "fillGaps": False,
            },
            "variables": {},
        }

    def _call_signoz(self, payload: dict) -> list[dict]:
        """Make a POST request to SigNoz query_range and return parsed rows."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        print(f"[SigNoz] POST {self.api_url}")
        print(f"[SigNoz] Auth token (first 20 chars): {self.api_key[:20]}...")
        print(f"[SigNoz] Payload filter: {payload['compositeQuery']['queries'][0]['spec']['filter']}")

        resp = requests.post(self.api_url, json=payload, headers=headers, timeout=30)

        if resp.status_code != 200:
            print(f"[SigNoz] HTTP {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()

        body = resp.json()

        if body.get("status") != "success":
            print(f"[SigNoz] API returned non-success status: {body}")
            return []

        rows = body.get("data", {}).get("data", {}).get("results", [{}])[0].get("rows", [])
        print(f"[SigNoz] Got {len(rows)} rows")
        return [row.get("data", row) for row in rows]

    def fetch_logs(self, query: str) -> list[dict[str, Any]]:
        """
        Fetch logs relevant to the given error query.

        If api_url and api_key are configured, queries SigNoz directly.
        Otherwise falls back to mock keyword matching.
        Returns a list of log entries sorted by timestamp.
        """
        # Try real SigNoz first
        if not self.use_mock and self.api_url and self.api_key:
            try:
                # Use body CONTAINS filter — SigNoz expects a filter expression, not free text
                expression = f"body CONTAINS '{query}'"
                payload = self._build_signoz_payload(expression)
                logs = self._call_signoz(payload)
                if logs:
                    print(f"[SigNoz] Returning {len(logs)} live logs")
                    return logs
                print("[SigNoz] No live logs found, falling back to mock data")
            except Exception as e:
                print(f"[SigNoz] API call failed: {e}, falling back to mock data")

        # Fallback: mock keyword matching
        query_lower = query.lower()
        matched_logs: list[dict] = []

        for scenario in self._scenarios:
            keywords = scenario.get("keywords", [])
            if any(kw in query_lower for kw in keywords):
                matched_logs.extend(scenario.get("logs", []))

        matched_logs.sort(key=lambda log: log.get("timestamp", ""))
        print(f"[SigNoz] Returning {len(matched_logs)} mock logs")
        return matched_logs

    def format_logs_for_prompt(self, logs: list[dict]) -> str:
        """Format log entries into a readable string for the LLM prompt."""
        if not logs:
            return "No relevant logs found in SigNoz."

        lines = []
        for log in logs:
            ts = log.get("timestamp", "unknown")
            severity = log.get("severity", "INFO")
            service = log.get("service", "unknown")
            message = log.get("message", "")
            attrs = log.get("attributes", {})

            line = f"[{ts}] [{severity}] [{service}] {message}"
            if attrs:
                attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
                line += f"\n  Attributes: {attr_str}"
            lines.append(line)

        return "\n\n".join(lines)
