"""
SigNoz client — fetches logs from the SigNoz Query API (v5/query_range).
"""

import re
import time
import requests
from typing import Any

# Default look-back window: 10 days in milliseconds
_DEFAULT_LOOKBACK_MS = 20 * 24 * 60 * 60 * 1000


class SigNozClient:
    def __init__(self, api_url: str = "", api_key: str = ""):
        self.api_url = api_url
        self.api_key = api_key

    # Severity priority for sorting logs before prompt formatting
    _SEVERITY_ORDER = {"error": 0, "err": 0, "warn": 1, "warning": 1, "info": 2, "debug": 3, "trace": 4}

    _PROMPT_CHAR_BUDGET = 30_000
    MAX_CORRELATION_IDS = 100
    CORRELATION_BATCH_SIZE = 15

    def _build_signoz_payload(self, expression: str, lookback_ms: int = _DEFAULT_LOOKBACK_MS, limit: int = 200) -> dict:
        """Build a SigNoz v5/query_range payload matching the JS issueAnalyzer format."""
        now = int(time.time() * 1000)
        # make start and end between 12 and 17
        # start = now - lookback_ms
        # end = now
        # if start < now - 17 * 24 * 60 * 60 * 1000:
        start = now - 18 * 24 * 60 * 60 * 1000
        # if end > now - 12 * 24 * 60 * 60 * 1000:
        end = now - 5 * 24 * 60 * 60 * 1000
        
        payload = {
            "schemaVersion": "v1",
            "start": start,
            "end": end,
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
        print('payload ------>', payload)
        return payload

    def _call_signoz(self, payload: dict) -> list[dict]:
        """Make a POST request to SigNoz query_range and return parsed rows."""
        headers = {
            "Content-Type": "application/json",
            "SIGNOZ-API-KEY": self.api_key,
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
        expression = f"endpoint = '{concrete_path}' AND severity_text != 'trace'"
        payload = self._build_signoz_payload(expression)
        base_logs = self._call_signoz(payload)
        print(f"[SigNoz] Base logs for {concrete_path}: {len(base_logs)}")

        if not base_logs:
            # Try a broader search with just the projectId
            expression = f"body CONTAINS '{project_id}' AND severity_text != 'trace'"
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

        # Step 3: Fetch error/warn context in batches of CORRELATION_BATCH_SIZE
        all_error_logs: list[dict] = []
        seen_span_ids: set[str] = set()
        corr_list = list(correlation_ids)[:self.MAX_CORRELATION_IDS]

        for i in range(0, len(corr_list), self.CORRELATION_BATCH_SIZE):
            batch = corr_list[i : i + self.CORRELATION_BATCH_SIZE]
            or_clauses = " OR ".join(f"correlationId='{cid}'" for cid in batch)
            expression = f"({or_clauses}) AND severity_text != 'trace'"
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
        return self._deduplicate_logs(combined)

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
            expression = f"body CONTAINS '{query}' AND severity_text != 'trace'"
            payload = self._build_signoz_payload(expression)
            logs = self._call_signoz(payload)
            logs = self._deduplicate_logs(logs)
            print(f"[SigNoz] Returning {len(logs)} logs")
            return logs
        except Exception as e:
            print(f"[SigNoz] API call failed: {e}")
            return []

    @staticmethod
    def _extract_log_fields(log: dict) -> dict:
        """Normalise a raw SigNoz log row into a flat dict with useful fields.

        SigNoz log structure:
          - top-level 'body': short summary (e.g. 'Connection established')
          - attributes_string.body: actual request/response payload (JSON string
            that may contain errorMessages, validation errors, etc.)
          - attributes_string.res: HTTP response details (JSON string)
          - attributes_string.err: error string if present
        """
        attrs = log.get("attributes_string", {})
        res = log.get("resources_string", {})

        # The real payload is in attributes_string.body, not top-level body
        attr_body = attrs.get("body", "")
        top_body = log.get("body", "")

        return {
            "timestamp": log.get("timestamp", "unknown"),
            "severity": log.get("severity_text", "INFO"),
            "service": (
                attrs.get("serviceName")
                or attrs.get("name")
                or res.get("service.name", "unknown")
            ),
            "body": top_body,
            "attr_body": attr_body,
            "response": attrs.get("res", ""),
            "error": attrs.get("err", ""),
            "endpoint": attrs.get("endpoint", ""),
            "method": attrs.get("method", ""),
            "correlationId": attrs.get("correlationId", ""),
            "requestId": attrs.get("requestId", ""),
            "trace_id": log.get("trace_id", ""),
            "span_id": log.get("span_id", ""),
        }

    @staticmethod
    def _deduplicate_logs(logs: list[dict]) -> list[dict]:
        """Remove exact duplicate log entries based on key fields."""
        seen: set[tuple] = set()
        unique: list[dict] = []
        for log in logs:
            attrs = log.get("attributes_string", log.get("attributes", {}))
            key = (
                log.get("timestamp", ""),
                log.get("body", ""),
                log.get("severity_text", ""),
                attrs.get("correlationId", ""),
                attrs.get("endpoint", ""),
            )
            if key not in seen:
                seen.add(key)
                unique.append(log)
        if len(unique) < len(logs):
            print(f"[SigNoz] Deduplicated {len(logs)} → {len(unique)} logs")
        return unique

    # Patterns that indicate an info-level log actually contains error content
    _ERROR_STATUS_RE = re.compile(r"\b[45]\d{2}\b")
    # Pattern for pure success access logs: "200 (123 ms) GET /some/path"
    _SUCCESS_ACCESS_RE = re.compile(r"^2\d{2}\s+\(\d+\s*ms\)\s+")

    @classmethod
    def _is_error_info_log(cls, log: dict) -> bool:
        """Check if an info-level log contains error-like content (4xx/5xx status, error keywords)."""
        body = log.get("body", "")
        attrs = log.get("attributes_string", {})
        err = attrs.get("err", "")
        # Has an explicit error attribute
        if err:
            return True
        # Body contains a 4xx or 5xx status code
        if cls._ERROR_STATUS_RE.search(body):
            return True
        # Body contains error-related keywords
        body_lower = body.lower()
        if any(kw in body_lower for kw in ("error", "fail", "exception", "validation error")):
            return True
        return False

    @classmethod
    def _is_success_access_log(cls, log: dict) -> bool:
        """Check if a log is a pure success access log (200 OK) with no error content."""
        sev = log.get("severity_text", "info").lower()
        if sev not in ("info", "debug"):
            return False
        body = log.get("body", "")
        attrs = log.get("attributes_string", {})
        # If it has an error attribute, it's not a pure success log
        if attrs.get("err", ""):
            return False
        # Match "200 (87 ms) GET /projects/..." pattern
        if cls._SUCCESS_ACCESS_RE.match(body):
            return True
        return False

    def format_logs_for_prompt(self, logs: list[dict]) -> str:
        """Format log entries into a readable string for the LLM prompt."""
        if not logs:
            return "No relevant logs found in SigNoz."

        # Option 2: Filter out pure success access logs (200 OK with no error content)
        filtered_logs = [log for log in logs if not self._is_success_access_log(log)]
        dropped = len(logs) - len(filtered_logs)
        if dropped:
            print(f"[SigNoz] Filtered out {dropped} success access logs (200 OK)")

        # Option 1: Sort by severity, but promote info logs that contain error content
        def _sev_key(log: dict) -> int:
            sev = log.get("severity_text", "info").lower()
            base = self._SEVERITY_ORDER.get(sev, 3)
            # Promote info-level logs with error content to warn-level priority
            if base >= 2 and self._is_error_info_log(log):
                return 1  # Same priority as warn
            return base

        sorted_logs = sorted(filtered_logs, key=_sev_key)

        lines = []
        total_chars = 0
        for log in sorted_logs:
            f = self._extract_log_fields(log)
            header = f"[{f['timestamp']}] [{f['severity']}] [{f['service']}]"

            parts = [header]

            if f["endpoint"]:
                parts.append(f"  Endpoint: {f['method']} {f['endpoint']}")
            if f["correlationId"]:
                parts.append(f"  CorrelationId: {f['correlationId']}")
            if f["body"]:
                parts.append(f"  Message: {f['body']}")

            # attributes_string.body has the real payload (request/response JSON)
            attr_body = f["attr_body"]
            if attr_body:
                if len(attr_body) > 500:
                    attr_body = attr_body[:500] + "… (truncated)"
                parts.append(f"  Payload: {attr_body}")

            # attributes_string.res has the HTTP response
            response = f["response"]
            if response:
                if len(response) > 300:
                    response = response[:300] + "… (truncated)"
                parts.append(f"  Response: {response}")

            # attributes_string.err has explicit error text
            if f["error"]:
                parts.append(f"  Error: {f['error']}")

            entry = "\n".join(parts)
            total_chars += len(entry) + 2  # +2 for separator
            if total_chars > self._PROMPT_CHAR_BUDGET:
                lines.append(f"… ({len(sorted_logs) - len(lines)} more logs omitted, budget reached)")
                break
            lines.append(entry)

        return "\n\n".join(lines)
