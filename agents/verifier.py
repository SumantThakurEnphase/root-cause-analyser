"""
Evidence Verifier — judges whether gathered evidence supports or refutes
a hypothesis, using a hybrid approach:

1. Rule-based pre-checks (cheap, deterministic):
   - "No results" check: empty evidence = weak refutation
   - "File/function exists" check: suspected file found in code search = partial support
   - "Error severity" check: log query found only info-level logs = prediction failed

2. LLM-as-judge (one Gemini call per hypothesis):
   - Judges all remaining unjudged evidence in a batch
   - Returns verdict + per-evidence judgments + follow_up_question for chaining
"""

from __future__ import annotations

import json
from typing import Optional

from agents.hypothesis import (
    Hypothesis,
    EvidenceItem,
    HypothesisStatus,
)
from services.gemini_client import GeminiClient


# ---------------------------------------------------------------------------
# Verification prompt — asks Gemini to judge evidence and seed chaining
# ---------------------------------------------------------------------------
VERIFICATION_PROMPT = """You are evaluating evidence for a root-cause hypothesis about the Solargraf platform.

## Hypothesis
- **Category:** {category}
- **Claim:** {claim}
- **Suspected file:** {suspected_file}
- **Suspected function:** {suspected_function}

## Predictions (what we expected to find)
{predictions}

## Evidence collected
{evidence}

## Instructions

1. For each piece of evidence, decide whether it **supports** or **contradicts** the hypothesis. Provide brief reasoning.
2. Considering ALL evidence together, render a **verdict**: is the hypothesis supported, refuted, or inconclusive?
3. Assign an overall **confidence** score (0.0 to 1.0).
4. If the hypothesis IS supported, propose a **follow-up question** — the next "why?" to trace the causal chain deeper. For example: "Why was this validation logic implemented this way? Was it copied from another module or was it a deliberate choice?" If refuted or inconclusive, set follow_up_question to an empty string.

Respond ONLY with a JSON object:
{{
  "verdict": "supported" | "refuted" | "inconclusive",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<one paragraph explaining the overall judgment>",
  "evidence_judgments": [
    {{"reference": "<evidence reference>", "supports": <true or false>, "reasoning": "<why>"}}
  ],
  "follow_up_question": "<next why question if supported, else empty string>"
}}
"""


class EvidenceVerifier:
    """Hybrid verifier: rule-based pre-checks + LLM-as-judge."""

    def __init__(self, gemini: GeminiClient):
        self.gemini = gemini

    async def verify(self, hypothesis: Hypothesis) -> VerificationResult:
        """Run rule-based checks, then LLM verification on remaining evidence.

        Args:
            hypothesis: A Hypothesis with gathered (unjudged) evidence.

        Returns:
            VerificationResult with verdict, updated hypothesis, and
            optional follow_up_question for causal chaining.
        """
        # Phase 1: Rule-based pre-checks
        self._apply_rule_checks(hypothesis)

        # Phase 2: LLM-as-judge for remaining unjudged evidence
        result = await self._llm_verify(hypothesis)

        # Phase 3: Update hypothesis status based on verdict
        if result.verdict == "supported":
            hypothesis.status = HypothesisStatus.SUPPORTED
        elif result.verdict == "refuted" or result.verdict == "inconclusive":
            hypothesis.status = HypothesisStatus.REFUTED
        hypothesis.confidence = result.confidence

        return result

    def _apply_rule_checks(self, hypothesis: Hypothesis) -> None:
        """Apply cheap deterministic checks to unjudged evidence items."""
        for item in hypothesis.evidence:
            if item.supports is not None:
                continue  # Already judged

            # Rule 1: "No results" check
            if self._is_no_results(item):
                item.supports = False
                item.reasoning = "No results returned for this prediction query"
                continue

            # Rule 2: "File/function exists" check
            if item.source == "code" and hypothesis.suspected_file:
                if self._file_matches(item, hypothesis.suspected_file):
                    item.supports = True
                    item.reasoning = f"Code snippet found from suspected file: {hypothesis.suspected_file}"
                    continue

            # Rule 3: "Error severity" check — log query expecting errors got only info
            if item.source == "log" and self._is_info_only_log(item):
                item.supports = False
                item.reasoning = "Log query returned only info-level content, no errors found"
                continue

    @staticmethod
    def _is_no_results(item: EvidenceItem) -> bool:
        """Check if evidence represents an empty search result."""
        content_lower = item.content.lower()
        return (
            content_lower.startswith("no code snippets found")
            or content_lower.startswith("no logs found")
            or content_lower.startswith("cannot execute")
            or content_lower.startswith("failed to execute")
        )

    @staticmethod
    def _file_matches(item: EvidenceItem, suspected_file: str) -> bool:
        """Check if a code evidence item comes from the suspected file."""
        # reference format from gatherer: "repo/file_path::function_name"
        ref = item.reference.lower()
        # Normalize the suspected file for comparison
        suspected = suspected_file.lower().strip("/")
        # Check if the suspected file path appears in the reference
        return suspected in ref or ref.endswith(suspected)

    @staticmethod
    def _is_info_only_log(item: EvidenceItem) -> bool:
        """Check if a log evidence item contains only info-level content."""
        content = item.content
        # The gatherer formats logs as: "[severity] [service] | ..."
        if content.startswith("[INFO]") or content.startswith("[info]"):
            # Check there's no error-related content
            lower = content.lower()
            return not any(kw in lower for kw in ("error", "fail", "exception", "4xx", "5xx", "400", "401", "403", "404", "500", "502", "503"))
        return False

    async def _llm_verify(self, hypothesis: Hypothesis) -> VerificationResult:
        """Send hypothesis + evidence to Gemini for batch judgment."""
        # Format predictions for the prompt
        pred_lines = []
        for i, p in enumerate(hypothesis.predictions, 1):
            pred_lines.append(f"{i}. [{p.search_type}] Query: `{p.query}` — Expected: {p.expected}")
        predictions_text = "\n".join(pred_lines) if pred_lines else "No predictions specified."

        # Format evidence for the prompt
        evidence_lines = []
        for i, e in enumerate(hypothesis.evidence, 1):
            status = "UNJUDGED"
            if e.supports is True:
                status = "SUPPORTS (rule-based)"
            elif e.supports is False:
                status = "CONTRADICTS (rule-based)"

            content = e.content
            if len(content) > 800:
                content = content[:800] + "... (truncated)"

            evidence_lines.append(
                f"### Evidence {i} [{e.source}] — {status}\n"
                f"Reference: {e.reference}\n"
                f"Content: {content}"
            )
        evidence_text = "\n\n".join(evidence_lines) if evidence_lines else "No evidence collected."

        prompt = VERIFICATION_PROMPT.format(
            category=hypothesis.category,
            claim=hypothesis.claim,
            suspected_file=hypothesis.suspected_file or "unknown",
            suspected_function=hypothesis.suspected_function or "unknown",
            predictions=predictions_text,
            evidence=evidence_text,
        )

        response = await self.gemini.analyze(prompt)
        return self._parse_verification_response(response, hypothesis)

    def _parse_verification_response(
        self, response: str, hypothesis: Hypothesis
    ) -> VerificationResult:
        """Parse Gemini's verification JSON into a VerificationResult."""
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        try:
            data = json.loads(text.strip())
        except (json.JSONDecodeError, AttributeError):
            print(f"[Verifier] Failed to parse Gemini response: {text[:200]}")
            return VerificationResult(
                verdict="inconclusive",
                confidence=0.3,
                reasoning="Could not parse verification response from Gemini.",
                follow_up_question="",
            )

        # Apply per-evidence judgments from Gemini to unjudged items
        judgments = data.get("evidence_judgments", [])
        for judgment in judgments:
            ref = judgment.get("reference", "")
            supports = judgment.get("supports", None)
            reasoning = judgment.get("reasoning", "")

            # Find the matching unjudged evidence item
            for item in hypothesis.evidence:
                if item.supports is None and ref and ref in item.reference:
                    item.supports = supports
                    item.reasoning = reasoning
                    break

        # Recalculate confidence from all judged evidence
        judged = [e for e in hypothesis.evidence if e.supports is not None]
        if judged:
            supporting = sum(1 for e in judged if e.supports)
            hypothesis.confidence = round(supporting / len(judged), 2)

        return VerificationResult(
            verdict=data.get("verdict", "inconclusive"),
            confidence=data.get("confidence", hypothesis.confidence),
            reasoning=data.get("reasoning", ""),
            follow_up_question=data.get("follow_up_question", ""),
        )


class VerificationResult:
    """Result of verifying a hypothesis against its evidence."""

    def __init__(
        self,
        verdict: str,
        confidence: float,
        reasoning: str,
        follow_up_question: str = "",
    ):
        self.verdict = verdict          # "supported", "refuted", "inconclusive"
        self.confidence = confidence    # 0.0 to 1.0
        self.reasoning = reasoning      # Explanation paragraph
        self.follow_up_question = follow_up_question  # Next "why?" for chaining

    def __repr__(self) -> str:
        return (
            f"VerificationResult(verdict={self.verdict!r}, "
            f"confidence={self.confidence}, "
            f"follow_up={self.follow_up_question[:50]!r})"
        )
