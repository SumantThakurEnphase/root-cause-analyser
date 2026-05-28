"""
SigNoz client — fetches logs from the SigNoz Query API (v5/query_range).
"""

import time
import requests
from typing import Any

# Default look-back window: 10 days in milliseconds
_DEFAULT_LOOKBACK_MS = 10 * 24 * 60 * 60 * 1000


class SigNozClient:
    def __init__(self, api_url: str = "", api_key: str = ""):
        self.api_url = api_url
        self.api_key = api_key

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

    def fetch_logs_by_api(
        self,
        api_path: str,
        project_id: str,
        proposal_id: str = "",
    ) -> list[dict[str, Any]]:
        """
        Fetch logs for a specific API endpoint and project, then follow
        correlationId chains to get full error/warn context.

        This mirrors the issueAnalyzer.js pattern:
        1. Query SigNoz for logs matching the API path + projectId
        2. Extract unique correlationIds from the results
        3. For each correlationId, fetch error/warn logs
        4. Return the combined, deduplicated error context

        Args:
            api_path: API route pattern (e.g., "/projects/:projectId/roofline").
            project_id: The project ID to filter on.
            proposal_id: Optional proposal ID for more precise filtering.

        Returns:
            List of log entries with error/warn context.
        """
        if not self.api_url or not self.api_key:
            print("[SigNoz] API URL or key not configured — cannot fetch logs")
            return []

        # Replace route params with the actual IDs
        concrete_path = api_path.replace(":projectId", project_id)
        if proposal_id:
            concrete_path = concrete_path.replace(":proposalId", proposal_id)

        try:
            return self._fetch_logs_by_api_live(concrete_path, project_id)
        except Exception as e:
            print(f"[SigNoz] API call failed: {e}")
            return []

    def _fetch_logs_by_api_live(
        self,
        concrete_path: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Live SigNoz query: find logs by API path, then follow correlationIds."""
        # Step 1: Find base logs matching the API endpoint + projectId
        expression = f"endpoint = '{concrete_path}'"
        payload = self._build_signoz_payload(expression)
        base_logs = self._call_signoz(payload)
        print(f"[SigNoz] Base logs for {concrete_path}: {len(base_logs)}")

        if not base_logs:
            # Try a broader search with just the projectId
            expression = f"body CONTAINS '{project_id}'"
            payload = self._build_signoz_payload(expression)
            base_logs = self._call_signoz(payload)
            print(f"[SigNoz] Broadened search for projectId {project_id}: {len(base_logs)}")

        if not base_logs:
            return []

        # Step 2: Extract unique correlationIds
        correlation_ids: set[str] = set()
        for log in base_logs:
            attrs = log.get("attributes_string", log.get("attributes", {}))
            corr_id = attrs.get("correlationId", "")
            if corr_id:
                correlation_ids.add(corr_id)

        if not correlation_ids:
            print("[SigNoz] No correlationIds found, returning base logs")
            return base_logs

        # Step 3: For each correlationId, fetch error/warn context
        all_error_logs: list[dict] = []
        seen_span_ids: set[str] = set()

        for corr_id in correlation_ids:
            expression = (
                f"correlationId='{corr_id}' AND "
                f"(severity_text = 'Error' OR severity_text='Warn' "
                f"or severity_text='warn')"
            )
            payload = self._build_signoz_payload(expression)
            error_logs = self._call_signoz(payload)

            for log in error_logs:
                span_id = log.get("span_id", log.get("spanID", ""))
                if span_id and span_id in seen_span_ids:
                    continue
                if span_id:
                    seen_span_ids.add(span_id)
                all_error_logs.append(log)

        print(
            f"[SigNoz] Followed {len(correlation_ids)} correlationId(s), "
            f"got {len(all_error_logs)} error/warn logs"
        )

        # Return base logs + error context, deduplicated
        combined = base_logs + all_error_logs
        return combined

    def fetch_logs(self, query: str) -> list[dict[str, Any]]:
        """
        Fetch logs relevant to the given error query from SigNoz.

        Args:
            query: Free-text search query (used in body CONTAINS filter).

        Returns:
            List of log entries sorted by timestamp.
        """
        if not self.api_url or not self.api_key:
            print("[SigNoz] API URL or key not configured — cannot fetch logs")
            return []

        try:
            expression = f"body CONTAINS '{query}'"
            payload = self._build_signoz_payload(expression)
            logs = self._call_signoz(payload)
            print(f"[SigNoz] Returning {len(logs)} logs")
            return logs
        except Exception as e:
            print(f"[SigNoz] API call failed: {e}")
            return []

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
