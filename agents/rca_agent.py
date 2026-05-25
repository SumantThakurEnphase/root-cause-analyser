"""
RCA Agent — orchestrates log fetching, code search, and LLM analysis
to produce a root cause analysis for a given error query.
"""

from services.signoz_client import SigNozClient
from services.code_search import CodeSearchService
from services.gemini_client import GeminiClient
from agents.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from config import config


class RCAAgent:
    def __init__(self):
        self.signoz = SigNozClient(
            api_url=config.SIGNOZ_API_URL,
            api_key=config.SIGNOZ_API_KEY,
            use_mock=config.SIGNOZ_USE_MOCK,
        )
        self.code_search = CodeSearchService()
        self.gemini = GeminiClient()

    async def analyze(self, query: str) -> str:
        """
        Run the full RCA pipeline:
        1. Fetch relevant logs from SigNoz (mock)
        2. Search codebase via ChromaDB for related code
        3. Build prompt with all context
        4. Call Gemini for analysis
        5. Return formatted response

        Args:
            query: The error description from the user.

        Returns:
            Formatted RCA string from Gemini.
        """
        # Step 1: Fetch logs
        logs = self.signoz.fetch_logs(query)
        formatted_logs = self.signoz.format_logs_for_prompt(logs)

        # Step 2: Build a search query from the user query + key log info
        search_query = self._build_search_query(query, logs)
        snippets = self.code_search.search(search_query)
        formatted_snippets = self.code_search.format_snippets_for_prompt(snippets)

        # Step 3: Build the full prompt
        user_prompt = USER_PROMPT_TEMPLATE.format(
            query=query,
            logs=formatted_logs,
            code_snippets=formatted_snippets,
        )
        full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"

        # Step 4: Call Gemini
        response = await self.gemini.analyze(full_prompt)

        return response

    def _build_search_query(self, query: str, logs: list[dict]) -> str:
        """
        Build an enriched search query by extracting key terms from logs.
        This improves ChromaDB retrieval by including function names,
        file paths, and error types found in the logs.
        """
        parts = [query]

        for log in logs:
            attrs = log.get("attributes", {})
            # Extract file paths and function names from log attributes
            if "file" in attrs:
                parts.append(attrs["file"])
            if "function" in attrs:
                parts.append(attrs["function"])
            if "error_type" in attrs:
                parts.append(attrs["error_type"])

            # Also look for function/file references in the message
            message = log.get("message", "")
            if message:
                parts.append(message)

        # Deduplicate while preserving order, then join
        seen = set()
        unique_parts = []
        for part in parts:
            if part not in seen:
                seen.add(part)
                unique_parts.append(part)

        return " ".join(unique_parts[:10])  # Limit to avoid overly long queries
