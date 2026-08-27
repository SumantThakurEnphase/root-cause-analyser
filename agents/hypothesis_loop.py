"""
Hypothesis-Test Loop — the core orchestrator for the multi-layer RCA pipeline.

Replaces the single Gemini call in the old pipeline with an iterative loop:
1. Generate 2-3 hypotheses from initial evidence
2. For each hypothesis: gather targeted evidence → verify
3. For the best supported hypothesis: deepen the causal chain (up to depth 2)
4. Render the final causal chain as Markdown + JSON

Control flow:
    bootstrap (existing steps 1-3) → generate hypotheses → for each:
        gather evidence → verify → if supported, deepen → render output

Budget: max 10 Gemini calls per analysis.
Fallback: if all hypotheses fail, return to single-shot pipeline.
"""

from __future__ import annotations

import json
from typing import Optional

from agents.hypothesis import (
    Hypothesis,
    HypothesisStatus,
    CausalChain,
)
from agents.evidence_gatherer import EvidenceGatherer
from agents.verifier import EvidenceVerifier, VerificationResult
from agents.output_renderer import OutputRenderer
from services.gemini_client import GeminiClient
from services.code_search import CodeSearchService
from services.signoz_client import SigNozClient


# ---------------------------------------------------------------------------
# Prompt for hypothesis generation from initial evidence
# ---------------------------------------------------------------------------
HYPOTHESIS_GENERATION_PROMPT = """You are an expert Root Cause Analyser for the Solargraf/Roofgraf platform.

Given the following issue report and initial evidence, generate 2-3 competing hypotheses about what caused this issue. Each hypothesis should be a different possible explanation.

## Issue
{query}

## Project Context
- Project ID: {project_id}
- API endpoints investigated: {api_info}

## Initial Logs
{logs}

## Initial Code Snippets
{code_snippets}

## Instructions

For each hypothesis, provide:
1. A cause category (code_bug, config_issue, infrastructure, third_party, data_issue, expected_behavior, unknown)
2. A clear claim about what went wrong
3. The suspected file and function (if you can identify them)
4. 1-2 testable predictions — specific searches to run to verify this hypothesis

Respond ONLY with a JSON array of hypothesis objects:
```json
[
  {{
    "category": "code_bug",
    "claim": "The validation function rejects valid input due to an off-by-one error",
    "suspected_file": "services/feature/validation.js",
    "suspected_function": "validateInput",
    "predictions": [
      {{
        "search_type": "code_search",
        "query": "validateInput validation boundary check",
        "expected": "A boundary check that uses < instead of <=",
        "description": "Search for the validation function to inspect boundary logic"
      }},
      {{
        "search_type": "log_query",
        "query": "severity_text = 'error' AND body CONTAINS 'validation failed'",
        "expected": "Error logs showing validation rejecting valid input",
        "description": "Look for validation error logs"
      }}
    ]
  }}
]
```

Valid search_type values: "code_search", "log_query", "log_query_api"
- code_search: natural language query for ChromaDB code search
- log_query: SigNoz filter expression (use: severity_text, endpoint, body, correlationId, serviceName, method with =, !=, CONTAINS, AND, OR)
- log_query_api: an API route path like "/projects/:projectId/financing/concert/products" (requires project context)

## Anomaly Patterns to Consider
- If the logs show a burst of write calls (POST/PUT/DELETE) to the same endpoint in rapid succession, consider whether duplicate calls could have created duplicate records or corrupted state.
- Common causes of duplicate writes: frontend retry loop, race condition, missing idempotency guard, double-click, or stale browser tab submitting simultaneously.
- If a "Write-Endpoint Bursts Detected" section is present in the logs, always generate at least one hypothesis that considers the burst as a potential cause.
"""

# ---------------------------------------------------------------------------
# Prompt for generating a child hypothesis from a follow-up question
# ---------------------------------------------------------------------------
CHAIN_DEEPENING_PROMPT = """You are tracing the causal chain of a root cause analysis for the Solargraf platform.

A hypothesis has been confirmed. Now we need to go one level deeper: WHY did this happen?

## Confirmed Hypothesis
- **Claim:** {parent_claim}
- **Category:** {parent_category}
- **File:** {parent_file}
- **Evidence:** {parent_evidence}

## Follow-up Question
{follow_up_question}

## Causal Chain So Far
{chain_summary}

## Instructions

Generate exactly ONE hypothesis that answers the follow-up question. Include testable predictions.

Respond ONLY with a JSON object (not an array):
```json
{{
  "category": "code_bug",
  "claim": "The helper function was reused from a different module where the behavior was correct",
  "suspected_file": "services/shared/helpers.js",
  "suspected_function": "processItems",
  "predictions": [
    {{
      "search_type": "code_search",
      "query": "processItems shared helper",
      "expected": "The same logic pattern used in a different context where it works correctly",
      "description": "Search for the shared helper to confirm reuse origin"
    }}
  ]
}}
```
"""


class HypothesisLoop:
    """Orchestrates the hypothesis-test-chain loop for multi-layer RCA."""

    MAX_GEMINI_CALLS = 10  # Hard budget cap
    MAX_HYPOTHESES = 3     # Max top-level hypotheses to generate

    def __init__(
        self,
        gemini: GeminiClient,
        code_search: CodeSearchService,
        signoz: SigNozClient,
    ):
        self.gemini = gemini
        self.verifier = EvidenceVerifier(gemini)
        self.renderer = OutputRenderer()
        self._code_search = code_search
        self._signoz = signoz
        self._gemini_call_count = 0

    async def run(
        self,
        query: str,
        project_id: str = "",
        proposal_id: str = "",
        api_info: str = "",
        initial_logs: str = "",
        initial_code: str = "",
    ) -> tuple[str, dict]:
        """Run the hypothesis-test loop.

        Args:
            query: Issue description.
            project_id: Solargraf project ID (from URL parsing).
            proposal_id: Solargraf proposal ID (from URL parsing).
            api_info: Formatted API discovery results.
            initial_logs: Formatted initial log dump.
            initial_code: Formatted initial code snippets.

        Returns:
            (markdown_output, json_output) — both representations of the result.
        """
        self._gemini_call_count = 0

        gatherer = EvidenceGatherer(
            code_search=self._code_search,
            signoz=self._signoz,
            project_id=project_id,
            proposal_id=proposal_id,
        )

        # Phase 1: Generate hypotheses
        hypotheses = await self._generate_hypotheses(
            query, project_id, api_info, initial_logs, initial_code
        )

        if not hypotheses:
            print("[HypothesisLoop] No hypotheses generated — returning empty")
            chain = CausalChain()
            return (
                self.renderer.render_markdown(chain, query),
                self.renderer.render_json(chain, query),
            )

        # Phase 2: Test each hypothesis (gather + verify)
        best_hypothesis: Optional[Hypothesis] = None
        best_result: Optional[VerificationResult] = None
        chain = CausalChain()

        for h in hypotheses:
            if self._budget_exhausted():
                print(f"[HypothesisLoop] Budget exhausted ({self._gemini_call_count}/{self.MAX_GEMINI_CALLS})")
                break

            print(f"[HypothesisLoop] Testing hypothesis: {h.claim[:80]}")

            # Gather evidence (no Gemini calls — just service calls)
            gatherer.gather(h)

            # Verify (1 Gemini call)
            result = await self._verify_with_budget(h)
            if result is None:
                continue

            chain.add(h)

            if result.verdict == "supported":
                if best_hypothesis is None or h.confidence > best_hypothesis.confidence:
                    best_hypothesis = h
                    best_result = result

        # Phase 3: Deepen the best supported hypothesis
        if best_hypothesis and best_result:
            best_hypothesis.status = HypothesisStatus.CONFIRMED
            await self._deepen_chain(
                chain, best_hypothesis, best_result, gatherer, query
            )

        # If no hypothesis was supported, mark the best one as confirmed anyway
        if not any(h.status == HypothesisStatus.CONFIRMED for h in chain.hypotheses):
            # Pick the highest-confidence hypothesis
            if chain.hypotheses:
                best = max(chain.hypotheses, key=lambda h: h.confidence)
                best.status = HypothesisStatus.CONFIRMED

        print(f"[HypothesisLoop] Done — {self._gemini_call_count} Gemini calls, chain depth {chain.depth}")

        md = self.renderer.render_markdown(chain, query)
        js = self.renderer.render_json(chain, query)
        return md, js

    async def _generate_hypotheses(
        self,
        query: str,
        project_id: str,
        api_info: str,
        logs: str,
        code: str,
    ) -> list[Hypothesis]:
        """Ask Gemini to generate 2-3 competing hypotheses."""
        prompt = HYPOTHESIS_GENERATION_PROMPT.format(
            query=query,
            project_id=project_id or "N/A",
            api_info=api_info or "No API discovery performed.",
            logs=logs or "No logs available.",
            code_snippets=code or "No code snippets available.",
        )

        response = await self._call_gemini(prompt)
        if response is None:
            return []

        return self._parse_hypotheses(response)

    async def _verify_with_budget(self, hypothesis: Hypothesis) -> Optional[VerificationResult]:
        """Verify a hypothesis, counting against the Gemini budget."""
        if self._budget_exhausted():
            return None

        self._gemini_call_count += 1  # Pre-count the verification call
        try:
            result = await self.verifier.verify(hypothesis)
            print(
                f"[HypothesisLoop] Verdict: {result.verdict} "
                f"(confidence: {result.confidence:.0%})"
            )
            return result
        except Exception as e:
            print(f"[HypothesisLoop] Verification failed: {e}")
            hypothesis.status = HypothesisStatus.REFUTED
            return None

    async def _deepen_chain(
        self,
        chain: CausalChain,
        parent: Hypothesis,
        parent_result: VerificationResult,
        gatherer: EvidenceGatherer,
        query: str,
    ) -> None:
        """Follow the causal chain deeper using follow_up_questions."""
        follow_up = parent_result.follow_up_question
        current_parent = parent
        current_result = parent_result

        while True:
            # Check termination conditions
            should_continue, reason = chain.should_deepen(
                follow_up, child_claim=""
            )
            if not should_continue:
                print(f"[HypothesisLoop] Chain stopped: {reason}")
                break

            if self._budget_exhausted():
                print(f"[HypothesisLoop] Chain stopped: budget exhausted")
                break

            # Generate child hypothesis from follow-up question
            child = await self._generate_child_hypothesis(
                current_parent, follow_up, chain
            )
            if child is None:
                print("[HypothesisLoop] Chain stopped: could not generate child hypothesis")
                break

            # Check cycle detection with the actual claim
            should_continue, reason = chain.should_deepen(
                follow_up, child_claim=child.claim
            )
            if not should_continue:
                print(f"[HypothesisLoop] Chain stopped: {reason}")
                break

            # Gather + verify the child
            gatherer.gather(child)
            result = await self._verify_with_budget(child)

            if result is None:
                break

            chain.add(child)

            if result.verdict != "supported":
                print(f"[HypothesisLoop] Child not supported — chain stops at depth {child.depth}")
                break

            # Check confidence termination
            should_terminate, reason = chain.check_confidence_termination(child)
            if should_terminate:
                print(f"[HypothesisLoop] Chain stopped: {reason}")
                break

            child.status = HypothesisStatus.CONFIRMED
            follow_up = result.follow_up_question
            current_parent = child
            current_result = result

    async def _generate_child_hypothesis(
        self,
        parent: Hypothesis,
        follow_up_question: str,
        chain: CausalChain,
    ) -> Optional[Hypothesis]:
        """Generate a single child hypothesis from a follow-up question."""
        parent_evidence = ""
        supporting = [e for e in parent.evidence if e.supports is True]
        if supporting:
            parent_evidence = "; ".join(
                f"{e.source}: {e.reasoning}" for e in supporting[:3]
            )

        prompt = CHAIN_DEEPENING_PROMPT.format(
            parent_claim=parent.claim,
            parent_category=parent.category,
            parent_file=parent.suspected_file or "unknown",
            parent_evidence=parent_evidence or "No specific evidence cited.",
            follow_up_question=follow_up_question,
            chain_summary=chain.to_prompt_summary(),
        )

        response = await self._call_gemini(prompt)
        if response is None:
            return None

        hypotheses = self._parse_hypotheses(response, single=True)
        if not hypotheses:
            return None

        child = hypotheses[0]
        child.parent_id = parent.id
        child.depth = parent.depth + 1
        return child

    async def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini with budget tracking."""
        if self._budget_exhausted():
            print("[HypothesisLoop] Budget exhausted — skipping Gemini call")
            return None

        self._gemini_call_count += 1
        try:
            response = await self.gemini.analyze(prompt)
            if response.startswith("⚠️"):
                print(f"[HypothesisLoop] Gemini error: {response}")
                return None
            return response
        except Exception as e:
            print(f"[HypothesisLoop] Gemini call failed: {e}")
            return None

    def _budget_exhausted(self) -> bool:
        """Check if the Gemini call budget is used up."""
        return self._gemini_call_count >= self.MAX_GEMINI_CALLS

    @staticmethod
    def _parse_hypotheses(response: str, single: bool = False) -> list[Hypothesis]:
        """Parse Gemini's JSON response into Hypothesis objects."""
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"[HypothesisLoop] Failed to parse hypotheses JSON: {text[:200]}")
            return []

        # Handle both single object and array
        if isinstance(data, dict):
            data = [data]

        hypotheses = []
        for item in data[:3]:  # Cap at 3
            try:
                h = Hypothesis.from_gemini_dict(item)
                hypotheses.append(h)
            except Exception as e:
                print(f"[HypothesisLoop] Skipping malformed hypothesis: {e}")
                continue

        return hypotheses
