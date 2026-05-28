"""
API Discovery service — identifies which backend API endpoint(s) are responsible
for a given feature/issue by searching the indexed codebase and using Gemini
to pick the most relevant routes.

Flow:
  1. Semantic search ChromaDB for route definitions matching the issue description
  2. Ask Gemini to select the most likely API endpoint(s) from the results
"""

import json
from dataclasses import dataclass
from typing import Optional

from services.code_search import CodeSearchService
from services.gemini_client import GeminiClient


API_DISCOVERY_PROMPT = """You are an API route discovery assistant for the Solargraf platform.

Given a user's issue description and code snippets from the codebase, identify which
backend API endpoint(s) are most likely responsible for handling this feature.

The platform uses Express.js routers in solargraf-api with patterns like:
- router.get('/projects/:projectId/proposals/:proposalId/siteplan', ...)
- router.post('/projects/:projectId/autoDesign', ...)
- app.use('/api/v1/projects', projectRouter)

Respond ONLY with a JSON array of objects. Each object must have:
- "api_path": The API route pattern (e.g., "/projects/:projectId/proposals/:proposalId/roofline")
- "method": HTTP method (GET, POST, PUT, DELETE)
- "file": Source file where the route is defined
- "confidence": "high", "medium", or "low"
- "reasoning": Brief explanation of why this endpoint is relevant

If no relevant endpoints are found, return an empty array: []

Example response:
[
  {
    "api_path": "/projects/:projectId/proposals/:proposalId/roofline",
    "method": "POST",
    "file": "services/roofline-service/src/routes.js",
    "confidence": "high",
    "reasoning": "This endpoint handles roofline detection for a proposal"
  }
]
"""


@dataclass
class DiscoveredAPI:
    """Represents a discovered API endpoint related to the user's issue."""

    api_path: str
    method: str
    file: str
    confidence: str
    reasoning: str


class APIDiscoveryService:
    def __init__(
        self,
        code_search: Optional[CodeSearchService] = None,
        gemini: Optional[GeminiClient] = None,
    ):
        self.code_search = code_search or CodeSearchService()
        self.gemini = gemini or GeminiClient()

    async def discover_apis(
        self,
        issue_description: str,
        url_path: str = "",
    ) -> list[DiscoveredAPI]:
        """
        Discover backend API endpoints responsible for the described feature.

        Args:
            issue_description: User's description of the issue (e.g., "roofline detection not working").
            url_path: Optional URL path from the Solargraf app for additional context.

        Returns:
            List of DiscoveredAPI objects, sorted by confidence.
        """
        # Build a search query focused on route/endpoint discovery
        search_terms = self._build_search_query(issue_description, url_path)

        # Search codebase for route definitions and related code
        snippets = self.code_search.search(search_terms, top_k=15, repo_filter="solargraf-api")

        if not snippets:
            # Broaden search to all repos
            snippets = self.code_search.search(search_terms, top_k=15)

        if not snippets:
            print(f"[APIDiscovery] No code snippets found for: {search_terms}")
            return []

        # Format snippets for the Gemini prompt
        formatted_snippets = self.code_search.format_snippets_for_prompt(snippets)

        # Ask Gemini to identify the relevant API endpoints
        prompt = (
            f"{API_DISCOVERY_PROMPT}\n\n"
            f"## Issue Description\n{issue_description}\n\n"
            f"## URL Path Context\n{url_path or 'Not provided'}\n\n"
            f"## Code Snippets from Codebase\n{formatted_snippets}"
        )

        response = await self.gemini.analyze(prompt)
        apis = self._parse_gemini_response(response)

        print(
            f"[APIDiscovery] Found {len(apis)} API endpoint(s) for issue: "
            f"{issue_description[:60]}..."
        )
        return apis

    def _build_search_query(self, issue_description: str, url_path: str) -> str:
        """Build a search query that targets route definitions and controllers."""
        parts = [issue_description]

        # Add route-related keywords to bias search toward endpoint definitions
        parts.append("router route endpoint controller")

        # Extract useful segments from URL path
        if url_path:
            # Remove IDs from path, keep resource names
            segments = [
                seg for seg in url_path.strip("/").split("/")
                if seg and not seg.isdigit() and len(seg) < 40
            ]
            parts.extend(segments)

        return " ".join(parts)

    @staticmethod
    def _parse_gemini_response(response: str) -> list[DiscoveredAPI]:
        """Parse Gemini's JSON response into DiscoveredAPI objects."""
        # Extract JSON from response (Gemini may wrap it in markdown code blocks)
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1]
            text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            text = text.split("```", 1)[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            print(f"[APIDiscovery] Failed to parse Gemini response as JSON: {text[:200]}")
            return []

        if not isinstance(data, list):
            return []

        apis = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                apis.append(
                    DiscoveredAPI(
                        api_path=item.get("api_path", ""),
                        method=item.get("method", "GET"),
                        file=item.get("file", "unknown"),
                        confidence=item.get("confidence", "low"),
                        reasoning=item.get("reasoning", ""),
                    )
                )
            except (KeyError, TypeError):
                continue

        # Sort by confidence: high > medium > low
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        apis.sort(key=lambda a: confidence_order.get(a.confidence, 3))

        return apis

    @staticmethod
    def format_apis_for_prompt(apis: list[DiscoveredAPI]) -> str:
        """Format discovered APIs into a readable string for the LLM prompt."""
        if not apis:
            return "No relevant API endpoints discovered."

        lines = []
        for i, api in enumerate(apis, 1):
            lines.append(
                f"{i}. [{api.confidence.upper()}] {api.method} {api.api_path}\n"
                f"   File: {api.file}\n"
                f"   Reason: {api.reasoning}"
            )

        return "\n\n".join(lines)
