"""
API Discovery service — identifies which backend API endpoint(s) are responsible
for a given feature/issue by sending the full route registry to Gemini.

Flow:
  1. Load the static route registry (services/route_registry.json)
  2. Pass the full route list + issue description to Gemini
  3. Gemini selects the most relevant route(s) from the registry
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

from services.gemini_client import GeminiClient

_ROUTE_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "route_registry.json",
)

API_DISCOVERY_PROMPT = """You are an API route discovery assistant for the Solargraf platform.

Below is the COMPLETE list of API routes registered in solargraf-api.
Given the user's issue description (and optional URL context), pick the route(s)
most likely responsible for the described feature or failure.

IMPORTANT:
- You MUST choose routes from the provided list. Do NOT invent routes.
- Pick 1–5 most relevant routes. Fewer is better if you are confident.
- Consider the URL path context: it hints which resource the user was looking at.

Respond ONLY with a JSON array of objects. Each object must have:
- "api_path": Exact path from the route list
- "method": One of the methods listed for that path (GET, POST, PUT, DELETE)
- "confidence": "high", "medium", or "low"
- "reasoning": Brief explanation of why this endpoint is relevant

If no relevant endpoints are found, return an empty array: []
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
        gemini: Optional[GeminiClient] = None,
    ):
        self.gemini = gemini or GeminiClient()
        self._routes: list[dict] = []
        self._load_route_registry()

    def _load_route_registry(self) -> None:
        """Load the static route registry from JSON."""
        try:
            with open(_ROUTE_REGISTRY_PATH, "r") as f:
                self._routes = json.load(f)
            print(f"[APIDiscovery] Loaded {len(self._routes)} routes from registry")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[APIDiscovery] Could not load route registry: {e}")
            self._routes = []

    @staticmethod
    def _format_route_list(routes: list[dict]) -> str:
        """Format routes into a compact string for the prompt."""
        lines = []
        for r in routes:
            methods = ", ".join(r["methods"])
            lines.append(f"{methods:30s} {r['path']}")
        return "\n".join(lines)

    async def discover_apis(
        self,
        issue_description: str,
        url_path: str = "",
    ) -> list[DiscoveredAPI]:
        """
        Discover backend API endpoints responsible for the described feature.

        Sends the full route registry + issue description to Gemini and lets
        it pick the most relevant routes. No local pre-filtering.

        Args:
            issue_description: User's description of the issue.
            url_path: Optional URL path from the Solargraf app for context.

        Returns:
            List of DiscoveredAPI objects, sorted by confidence.
        """
        if not self._routes:
            print("[APIDiscovery] No route registry loaded, cannot discover APIs")
            return []

        route_list_text = self._format_route_list(self._routes)
        prompt = (
            f"{API_DISCOVERY_PROMPT}\n\n"
            f"## All Registered Routes ({len(self._routes)} total)\n"
            f"```\n{route_list_text}\n```\n\n"
            f"## Issue Description\n{issue_description}\n\n"
            f"## URL Path Context\n{url_path or 'Not provided'}"
        )

        response = await self.gemini.analyze(prompt)
        apis = self._parse_gemini_response(response)

        print(
            f"[APIDiscovery] Found {len(apis)} API endpoint(s) for issue: "
            f"{issue_description[:60]}..."
        )
        return apis

    @staticmethod
    def _parse_gemini_response(response: str) -> list[DiscoveredAPI]:
        """Parse Gemini's JSON response into DiscoveredAPI objects."""
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
                        file=item.get("file", "router/index.js"),
                        confidence=item.get("confidence", "low"),
                        reasoning=item.get("reasoning", ""),
                    )
                )
            except (KeyError, TypeError):
                continue

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
                f"   Reason: {api.reasoning}"
            )

        return "\n\n".join(lines)
