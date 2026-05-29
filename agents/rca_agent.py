"""
RCA Agent — orchestrates the full incident analysis pipeline:

1. Parse URL → extract projectId, proposalId
2. Discover API endpoint(s) via codebase search + Gemini
3. Fetch SigNoz logs by API path + projectId + correlationId chaining
4. Classify the cause category (code bug, config, infra, etc.)
5. Run category-specific deep analysis with tailored prompt
"""

import json

from services.signoz_client import SigNozClient
from services.code_search import CodeSearchService
from services.gemini_client import GeminiClient
from services.input_parser import parse_input, extract_url_from_message, AnalysisRequest
from services.api_discovery import APIDiscoveryService
from agents.prompts import SYSTEM_PROMPT, CATEGORY_PROMPTS, USER_PROMPT_TEMPLATE
from agents.cause_categories import (
    CauseCategory,
    CATEGORY_LABELS,
    build_classifier_prompt,
)
from config import config


class RCAAgent:
    def __init__(self):
        self.signoz = SigNozClient(
            api_url=config.SIGNOZ_API_URL,
            api_key=config.SIGNOZ_API_KEY,
        )
        self.code_search = CodeSearchService()
        self.gemini = GeminiClient()
        self.api_discovery = APIDiscoveryService(
            gemini=self.gemini,
        )

    async def analyze(self, query: str, url: str = "") -> str:
        """
        Run the full RCA pipeline.

        If a URL is provided, runs the project-aware pipeline:
          1. Parse URL → projectId, proposalId
          2. Discover API endpoint(s) for the described feature
          3. Fetch SigNoz logs by API path + projectId
          4. Classify the cause category
          5. Run category-specific deep analysis

        If no URL is provided, falls back to the original query-based flow.

        Args:
            query: The issue description from the user.
            url: Optional Solargraf app URL with projectId.

        Returns:
            Formatted RCA string from Gemini.
        """
        # If no explicit URL provided, try to extract one from the query text
        if not url:
            extracted_url, remaining_query = extract_url_from_message(query)
            if extracted_url:
                url = extracted_url
                query = remaining_query or query

        if url:
            return await self._analyze_with_url(url, query)
        return await self._analyze_query_only(query)

    async def _analyze_with_url(self, url: str, query: str) -> str:
        """Project-aware pipeline: parse URL → discover API → logs → classify → analyze."""
        # Step 1: Parse URL
        try:
            request = parse_input(url, query)
        except ValueError as e:
            return f"⚠️ Could not parse URL: {e}"

        print(
            f"[RCA] Project: {request.project_id}, "
            f"Proposal: {request.proposal_id or 'N/A'}, "
            f"Issue: {request.issue_description[:80]}"
        )

        # Step 2: Discover API endpoint(s)
        discovered_apis = await self.api_discovery.discover_apis(
            issue_description=request.issue_description,
            url_path=request.url_path,
        )
        formatted_apis = APIDiscoveryService.format_apis_for_prompt(discovered_apis)

        # Step 3: Fetch SigNoz logs
        all_logs: list[dict] = []

        # Try API-specific log fetching for each discovered endpoint
        for api in discovered_apis:
            api_logs = self.signoz.fetch_logs_by_api(
                api_path=api.api_path,
                project_id=request.project_id,
                proposal_id=request.proposal_id or "",
            )
            all_logs.extend(api_logs)

        # If no logs found via API discovery, fall back to query-based search
        if not all_logs:
            print("[RCA] No logs from API discovery, falling back to query-based search")
            all_logs = self.signoz.fetch_logs(request.issue_description)

        formatted_logs = self.signoz.format_logs_for_prompt(all_logs)

        # Step 4: Classify the cause category
        category, classification = await self._classify_cause(
            request.issue_description, formatted_logs
        )
        print(f"[RCA] Classified as: {CATEGORY_LABELS.get(category, category)} ({classification})")

        # Step 5: Build category-specific prompt and run deep analysis
        system_prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["unknown"])

        # Search codebase for relevant code snippets
        search_query = self._build_search_query(request.issue_description, all_logs)
        snippets = self.code_search.search_with_call_chain(search_query)
        formatted_snippets = self.code_search.format_snippets_for_prompt(snippets)

        user_prompt = USER_PROMPT_TEMPLATE.format(
            query=request.issue_description,
            project_id=request.project_id,
            proposal_id=request.proposal_id or "N/A",
            discovered_apis=formatted_apis,
            logs=formatted_logs,
            code_snippets=formatted_snippets,
        )

        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        print(f"[RCA] Sending prompt to Gemini ({len(full_prompt)} chars)")

        response = await self.gemini.analyze(full_prompt)
        return response

    async def _analyze_query_only(self, query: str) -> str:
        """Original query-based flow (backward-compatible, no URL)."""
        # Step 1: Fetch logs
        logs = self.signoz.fetch_logs(query)
        formatted_logs = self.signoz.format_logs_for_prompt(logs)

        # Step 2: Classify cause
        category, classification = await self._classify_cause(query, formatted_logs)
        print(f"[RCA] Classified as: {CATEGORY_LABELS.get(category, category)} ({classification})")

        # Step 3: Build a search query from the user query + key log info
        search_query = self._build_search_query(query, logs)
        snippets = self.code_search.search_with_call_chain(search_query)
        formatted_snippets = self.code_search.format_snippets_for_prompt(snippets)

        # Step 4: Build the full prompt with category-specific system prompt
        system_prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["unknown"])

        user_prompt = USER_PROMPT_TEMPLATE.format(
            query=query,
            project_id="N/A",
            proposal_id="N/A",
            discovered_apis="No URL provided — using query-based search.",
            logs=formatted_logs,
            code_snippets=formatted_snippets,
        )

        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        print(f"[RCA] Sending prompt to Gemini ({len(full_prompt)} chars)")

        # Step 5: Call Gemini
        response = await self.gemini.analyze(full_prompt)
        return response

    async def _classify_cause(
        self, query: str, formatted_logs: str
    ) -> tuple[str, str]:
        """
        Use Gemini to classify the incident into a cause category.

        Returns:
            Tuple of (category_value, raw_classification_json).
        """
        classifier_prompt = build_classifier_prompt()
        classify_input = (
            f"{classifier_prompt}\n\n"
            f"## Issue Description\n{query}\n\n"
            f"## Log Summary\n{formatted_logs}"
        )

        response = await self.gemini.analyze(classify_input)

        # Parse the JSON response
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        try:
            result = json.loads(text.strip())
            category = result.get("category", "unknown")
            # Validate it's a known category
            valid_categories = {c.value for c in CauseCategory}
            if category not in valid_categories:
                category = "unknown"
            return category, json.dumps(result)
        except (json.JSONDecodeError, AttributeError):
            print(f"[RCA] Failed to parse classifier response: {text[:200]}")
            return "unknown", "{}"

    def _build_search_query(self, query: str, logs: list[dict]) -> str:
        """
        Build an enriched search query by extracting key terms from logs.
        This improves ChromaDB retrieval by including function names,
        file paths, error types, and endpoint info found in the logs.
        """
        parts = [query]

        for log in logs:
            attrs = log.get("attributes_string", {})
            # Extract endpoint and service info
            endpoint = attrs.get("endpoint", "")
            if endpoint:
                parts.append(endpoint)
            service = attrs.get("serviceName", "")
            if service:
                parts.append(service)

            # Extract error-relevant terms from body
            body = log.get("body", "")
            if body and len(body) < 300:
                parts.append(body)

        # Deduplicate while preserving order, then join
        seen = set()
        unique_parts = []
        for part in parts:
            if part not in seen:
                seen.add(part)
                unique_parts.append(part)

        return " ".join(unique_parts[:10])  # Limit to avoid overly long queries
