"""
Evidence Gatherer — executes TestPredictions from hypotheses and collects
raw evidence without judging whether it supports or refutes the claim.

The gatherer is the bridge between Gemini's predictions and the actual
evidence tools (ChromaDB code search, SigNoz log queries). It:
1. Takes a Hypothesis with TestPredictions
2. Executes each prediction using the appropriate service
3. Returns EvidenceItems with supports=None (unjudged)
4. The Verifier (ticket 03) later judges each piece of evidence
"""

from __future__ import annotations

from typing import Optional

from agents.hypothesis import (
    Hypothesis,
    TestPrediction,
    EvidenceItem,
    HypothesisStatus,
)
from services.code_search import CodeSearchService
from services.signoz_client import SigNozClient


class EvidenceGatherer:
    """Executes hypothesis predictions against code and log services."""

    # Max results per prediction to avoid overwhelming the verifier
    _CODE_SEARCH_TOP_K = 5
    _LOG_QUERY_LIMIT = 20
    # Max content length per evidence item (keeps prompt budget manageable)
    _MAX_CONTENT_CHARS = 1500

    def __init__(
        self,
        code_search: CodeSearchService,
        signoz: SigNozClient,
        project_id: str = "",
        proposal_id: str = "",
    ):
        self.code_search = code_search
        self.signoz = signoz
        self.project_id = project_id
        self.proposal_id = proposal_id

    def gather(self, hypothesis: Hypothesis) -> Hypothesis:
        """Execute all predictions on a hypothesis and attach raw evidence.

        Mutates the hypothesis in place: sets status to INVESTIGATING,
        executes each prediction, and appends unjudged EvidenceItems.

        Args:
            hypothesis: A Hypothesis with predictions to execute.

        Returns:
            The same Hypothesis with evidence attached.
        """
        hypothesis.status = HypothesisStatus.INVESTIGATING

        for prediction in hypothesis.predictions:
            items = self._execute_prediction(prediction)
            for item in items:
                hypothesis.add_evidence(item)

        return hypothesis

    def _execute_prediction(self, prediction: TestPrediction) -> list[EvidenceItem]:
        """Route a prediction to the appropriate service and collect results."""
        dispatch = {
            "code_search": self._execute_code_search,
            "log_query": self._execute_log_query,
            "log_query_api": self._execute_log_query_api,
        }

        handler = dispatch.get(prediction.search_type)
        if handler is None:
            print(f"[EvidenceGatherer] Unknown search_type: {prediction.search_type}")
            return []

        try:
            return handler(prediction)
        except Exception as e:
            print(f"[EvidenceGatherer] Error executing {prediction.search_type}: {e}")
            return [
                EvidenceItem(
                    source="error",
                    reference=prediction.search_type,
                    content=f"Failed to execute prediction: {e}",
                )
            ]

    def _execute_code_search(self, prediction: TestPrediction) -> list[EvidenceItem]:
        """Search ChromaDB for code snippets matching the prediction query."""
        snippets = self.code_search.search(
            query=prediction.query,
            top_k=self._CODE_SEARCH_TOP_K,
        )

        if not snippets:
            return [
                EvidenceItem(
                    source="code",
                    reference="ChromaDB",
                    content=f"No code snippets found for query: {prediction.query}",
                )
            ]

        items = []
        for s in snippets:
            code = s.get("code", "")
            if len(code) > self._MAX_CONTENT_CHARS:
                code = code[: self._MAX_CONTENT_CHARS] + "\n// ... (truncated)"

            fn_label = f"::{s.get('function_name', '')}" if s.get("function_name") else ""
            reference = f"{s.get('repo', 'unknown')}/{s.get('file_path', 'unknown')}{fn_label}"

            items.append(
                EvidenceItem(
                    source="code",
                    reference=reference,
                    content=code,
                )
            )

        return items

    def _execute_log_query(self, prediction: TestPrediction) -> list[EvidenceItem]:
        """Execute a SigNoz filter expression to find targeted logs."""
        logs = self.signoz.fetch_logs_by_expression(prediction.query)

        if not logs:
            return [
                EvidenceItem(
                    source="log",
                    reference="SigNoz",
                    content=f"No logs found for expression: {prediction.query}",
                )
            ]

        items = []
        for log in logs[: self._LOG_QUERY_LIMIT]:
            fields = SigNozClient._extract_log_fields(log)
            content = self._format_log_evidence(fields)

            items.append(
                EvidenceItem(
                    source="log",
                    reference=str(fields.get("timestamp", "unknown")),
                    content=str(content),
                )
            )

        return items

    def _execute_log_query_api(self, prediction: TestPrediction) -> list[EvidenceItem]:
        """Fetch logs by API path using the structured SigNoz method."""
        if not self.project_id:
            return [
                EvidenceItem(
                    source="log",
                    reference="SigNoz",
                    content="Cannot execute log_query_api without a project_id",
                )
            ]

        logs = self.signoz.fetch_logs_by_api(
            api_path=prediction.query,
            project_id=self.project_id,
            proposal_id=self.proposal_id,
        )

        if not logs:
            return [
                EvidenceItem(
                    source="log",
                    reference="SigNoz",
                    content=f"No logs found for API path: {prediction.query} (project: {self.project_id})",
                )
            ]

        items = []
        for log in logs[: self._LOG_QUERY_LIMIT]:
            fields = SigNozClient._extract_log_fields(log)
            content = self._format_log_evidence(fields)

            items.append(
                EvidenceItem(
                    source="log",
                    reference=str(fields.get("timestamp", "unknown")),
                    content=str(content),
                )
            )

        return items

    @staticmethod
    def _format_log_evidence(fields: dict) -> str:
        """Format extracted log fields into a compact evidence string."""
        parts = [f"[{fields.get('severity', 'INFO')}] [{fields.get('service', 'unknown')}]"]

        if fields.get("endpoint"):
            parts.append(f"Endpoint: {fields.get('method', '')} {fields['endpoint']}")
        if fields.get("body"):
            body = fields["body"]
            if len(body) > 500:
                body = body[:500] + "..."
            parts.append(f"Message: {body}")
        if fields.get("error"):
            parts.append(f"Error: {fields['error']}")
        if fields.get("correlationId"):
            parts.append(f"CorrelationId: {fields['correlationId']}")

        return " | ".join(parts)
