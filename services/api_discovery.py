"""
API Discovery service — identifies which backend API endpoint(s) are responsible
for a given feature/issue by matching against the actual route registry
(parsed from solargraf-api router/index.js) and using Gemini to rank them.

Flow:
  1. Load the static route registry (services/route_registry.json)
  2. Pass the full route list + issue description to Gemini
  3. Gemini selects the most relevant route(s) from the real registry
"""

import json
import os
import re
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
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful keywords from text for route matching."""
        # Remove URLs from text
        text = re.sub(r"https?://[^\s]+", "", text)
        # camelCase / PascalCase → separate words
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        # Split on non-alpha, lowercase, remove noise words
        tokens = re.split(r"[^a-zA-Z]+", text.lower())
        stop = {
            "the", "a", "an", "is", "was", "not", "for", "to", "of",
            "and", "in", "on", "i", "am", "are", "why", "how", "what",
            "this", "that", "my", "me", "it", "do", "does", "able",
            "can", "from", "with", "be", "https", "http", "com",
            "projects", "proposals",  # generic path segments
        }
        return [
            t for t in tokens
            if t and len(t) > 2 and t not in stop
            and not re.match(r"^[0-9a-f]+$", t)  # skip hex fragments (UUID parts)
        ]

    @staticmethod
    def _extract_path_segments(url_path: str) -> list[str]:
        """Extract meaningful path segments, filtering out IDs and UUIDs."""
        segments = []
        for seg in url_path.strip("/").split("/"):
            if not seg:
                continue
            # Skip numeric IDs
            if seg.isdigit():
                continue
            # Skip UUID-like strings
            if re.match(r"^[0-9a-f]{8}-", seg, re.IGNORECASE):
                continue
            # Skip very long hex strings
            if len(seg) > 20 and re.match(r"^[0-9a-f]+$", seg, re.IGNORECASE):
                continue
            # Skip generic resource names already in stop list
            if seg.lower() in ("projects", "proposals"):
                continue
            segments.append(seg)
        return segments

    def _pre_filter_routes(
        self, issue_description: str, url_path: str, max_routes: int = 40
    ) -> list[dict]:
        """Score and filter routes by keyword overlap with the issue."""
        keywords = self._extract_keywords(issue_description)
        if url_path:
            keywords.extend(self._extract_keywords(url_path))
            # Also keep raw path segments (e.g. 'permitPlanSet') for exact matching
            keywords.extend(seg.lower() for seg in self._extract_path_segments(url_path))
        keywords = list(dict.fromkeys(keywords))  # dedupe, preserve order

        if not keywords:
            return self._routes[:max_routes]

        scored: list[tuple[int, dict]] = []
        for route in self._routes:
            path_lower = route["path"].lower()
            # camelCase split on path too
            path_expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", path_lower).lower()
            score = sum(
                1 for kw in keywords
                if kw in path_lower or kw in path_expanded
            )
            if score > 0:
                scored.append((score, route))

        scored.sort(key=lambda x: -x[0])
        filtered = [r for _, r in scored[:max_routes]]

        print(
            f"[APIDiscovery] Keywords: {keywords}, "
            f"pre-filtered {len(self._routes)} → {len(filtered)} routes"
        )
        return filtered

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

        Uses keyword pre-filtering + Gemini to pick the best matches.
        Falls back to keyword-only matching if Gemini is unavailable.

        Args:
            issue_description: User's description of the issue.
            url_path: Optional URL path from the Solargraf app for context.

        Returns:
            List of DiscoveredAPI objects, sorted by confidence.
        """
        if not self._routes:
            print("[APIDiscovery] No route registry loaded, cannot discover APIs")
            return []

        # Pre-filter to a manageable subset
        candidate_routes = self._pre_filter_routes(issue_description, url_path)

        if not candidate_routes:
            print("[APIDiscovery] No routes matched keywords")
            return []

        # Try Gemini for intelligent ranking
        route_list_text = self._format_route_list(candidate_routes)
        prompt = (
            f"{API_DISCOVERY_PROMPT}\n\n"
            f"## Candidate Routes ({len(candidate_routes)} pre-filtered)\n"
            f"```\n{route_list_text}\n```\n\n"
            f"## Issue Description\n{issue_description}\n\n"
            f"## URL Path Context\n{url_path or 'Not provided'}"
        )

        response = await self.gemini.analyze(prompt)
        apis = self._parse_gemini_response(response)

        # Fallback: if Gemini failed (rate limit, parse error), use top keyword matches
        if not apis and candidate_routes:
            print("[APIDiscovery] Gemini failed, falling back to keyword-matched routes")
            for route in candidate_routes[:3]:
                apis.append(DiscoveredAPI(
                    api_path=route["path"],
                    method=route["methods"][0],
                    file="router/index.js",
                    confidence="medium",
                    reasoning="Keyword-matched from route registry (Gemini unavailable)",
                ))

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
