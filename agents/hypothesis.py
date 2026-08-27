"""
Hypothesis data model for the multi-layer RCA pipeline.

A hypothesis is a structured claim about what went wrong, with a testable
prediction and evidence tracking. It replaces the single CauseCategory
classification with a richer reasoning structure that the hypothesis-test
loop can act on.

Key differences from CauseCategory:
- CauseCategory is a label (one of 7 enum values)
- A Hypothesis is a claim + prediction + evidence chain
- Multiple hypotheses can coexist and compete during analysis
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class HypothesisStatus(str, Enum):
    """Lifecycle state of a hypothesis in the test loop."""
    PROPOSED = "proposed"       # Generated, not yet tested
    INVESTIGATING = "investigating"  # Evidence gathering in progress
    SUPPORTED = "supported"     # Evidence supports the claim
    REFUTED = "refuted"         # Evidence contradicts the claim
    CONFIRMED = "confirmed"     # Verified with high confidence


@dataclass
class EvidenceItem:
    """A single piece of evidence linked to a hypothesis."""
    source: str          # "log", "code", "api_discovery"
    reference: str       # Log timestamp, file:line, or API path
    content: str         # The actual log line, code snippet, or API info
    supports: Optional[bool] = None  # None = not yet judged, True = supports, False = contradicts
    reasoning: str = ""  # Why this evidence matters (filled by verifier)


@dataclass
class TestPrediction:
    """A testable prediction derived from a hypothesis.

    Encodes: "If this hypothesis is correct, then I should find X
    when I search Y." This drives targeted evidence gathering.
    """
    search_type: str     # "code_search", "log_query", "log_query_api"
    query: str           # The search query or file path to check
    expected: str        # What we expect to find if hypothesis is true
    description: str     # Human-readable prediction statement


@dataclass
class Hypothesis:
    """A structured claim about what caused an incident.

    Fields are designed to be:
    1. Producible by Gemini as JSON (flat, simple types)
    2. Actionable by the loop (predictions drive evidence gathering)
    3. Chainable (parent_id links causes into a causal chain)
    """
    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # The claim
    category: str = ""          # One of CauseCategory values (code_bug, data_issue, etc.)
    claim: str = ""             # Natural language: "The eligibility check counts panel rows instead of unique module types"
    suspected_file: str = ""    # "solargraf-api/services/financing/concert/eligibility.js"
    suspected_function: str = ""  # "checkModuleEligibility"
    suspected_line: str = ""    # "line 42" or "" if unknown

    # Testable predictions — what to look for to verify/refute
    predictions: list[TestPrediction] = field(default_factory=list)

    # Evidence collected during investigation
    evidence: list[EvidenceItem] = field(default_factory=list)

    # Status and confidence
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = 0.0     # 0.0 to 1.0, updated as evidence accumulates

    # Causal chaining
    parent_id: Optional[str] = None  # ID of the hypothesis this one explains ("why?")
    depth: int = 0                   # 0 = top-level, 1 = first "why?", 2 = second "why?"

    def add_evidence(self, item: EvidenceItem) -> None:
        """Add evidence and update confidence heuristically.

        Only judged evidence (supports is not None) affects confidence.
        Unjudged evidence is stored but doesn't change the score.
        """
        self.evidence.append(item)
        judged = [e for e in self.evidence if e.supports is not None]
        if judged:
            supporting = sum(1 for e in judged if e.supports)
            self.confidence = round(supporting / len(judged), 2)

    def to_prompt_dict(self) -> dict:
        """Serialize to a dict suitable for embedding in a Gemini prompt.

        Keeps only the fields the LLM needs to reason about — omits
        internal tracking fields like id, status, and raw evidence content.
        """
        d = {
            "category": self.category,
            "claim": self.claim,
            "confidence": self.confidence,
        }
        if self.suspected_file:
            d["suspected_file"] = self.suspected_file
        if self.suspected_function:
            d["suspected_function"] = self.suspected_function
        if self.predictions:
            d["predictions"] = [
                {"search_type": p.search_type, "query": p.query, "expected": p.expected}
                for p in self.predictions
            ]
        if self.evidence:
            d["evidence_summary"] = [
                {"source": e.source, "reference": e.reference, "supports": e.supports, "reasoning": e.reasoning}
                for e in self.evidence
            ]
        return d

    @classmethod
    def from_gemini_dict(cls, data: dict, parent_id: Optional[str] = None, depth: int = 0) -> Hypothesis:
        """Create a Hypothesis from Gemini's JSON output.

        Expected Gemini output format:
        {
            "category": "code_bug",
            "claim": "The eligibility check uses .length instead of Set.size",
            "suspected_file": "services/financing/concert/eligibility.js",
            "suspected_function": "checkModuleEligibility",
            "predictions": [
                {
                    "search_type": "code_search",
                    "query": "checkModuleEligibility eligibility",
                    "expected": "A function that checks panel array length",
                    "description": "If this is right, the function should use .length on the panels array"
                }
            ]
        }
        """
        predictions = []
        for p in data.get("predictions", []):
            predictions.append(TestPrediction(
                search_type=p.get("search_type", "code_search"),
                query=p.get("query", ""),
                expected=p.get("expected", ""),
                description=p.get("description", ""),
            ))

        return cls(
            category=data.get("category", "unknown"),
            claim=data.get("claim", ""),
            suspected_file=data.get("suspected_file", ""),
            suspected_function=data.get("suspected_function", ""),
            predictions=predictions,
            parent_id=parent_id,
            depth=depth,
        )


@dataclass
class CausalChain:
    """A linked chain of hypotheses representing the full causal path.

    Structure: symptom → cause → deeper cause → root cause
    Each node is a confirmed Hypothesis, linked via parent_id.
    """

    # Chaining control constants
    MAX_DEPTH = 2                # Max "why?" levels (0=top, 1=first why, 2=second why)
    MIN_CONFIDENCE = 0.4         # Terminate chain if confidence drops below this
    CYCLE_WORD_OVERLAP = 0.6     # Word overlap threshold for cycle detection

    hypotheses: list[Hypothesis] = field(default_factory=list)

    @property
    def root_cause(self) -> Optional[Hypothesis]:
        """The deepest confirmed hypothesis in the chain."""
        confirmed = [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]
        if not confirmed:
            return None
        return max(confirmed, key=lambda h: h.depth)

    @property
    def depth(self) -> int:
        """How many levels deep the chain goes."""
        if not self.hypotheses:
            return 0
        return max(h.depth for h in self.hypotheses) + 1

    def get_chain_path(self) -> list[Hypothesis]:
        """Return the chain from shallowest to deepest confirmed hypothesis."""
        confirmed = [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]
        return sorted(confirmed, key=lambda h: h.depth)

    def add(self, hypothesis: Hypothesis) -> None:
        """Add a hypothesis to the chain."""
        self.hypotheses.append(hypothesis)

    def should_deepen(self, follow_up_question: str, child_claim: str = "") -> tuple[bool, str]:
        """Check whether the chain should go one level deeper.

        Applies termination conditions in order:
        1. Max depth reached
        2. No follow-up question from verifier
        3. Cycle detection (word overlap with ancestor claims)

        The confidence check (MIN_CONFIDENCE) is applied after verification,
        not here — it's checked in the loop after the child is verified.

        Args:
            follow_up_question: The "why?" from the verifier. Empty = stop.
            child_claim: The proposed claim for the next depth level.
                         Used for cycle detection. If empty, skip cycle check.

        Returns:
            (should_continue, reason) — reason explains why we stopped.
        """
        current_depth = self.depth

        # Condition 1: Max depth
        if current_depth >= self.MAX_DEPTH:
            return False, f"Max depth reached ({self.MAX_DEPTH})"

        # Condition 2: No follow-up question
        if not follow_up_question or not follow_up_question.strip():
            return False, "No follow-up question from verifier"

        # Condition 3: Cycle detection
        if child_claim:
            cycle_ancestor = self._detect_cycle(child_claim)
            if cycle_ancestor:
                return False, f"Cycle detected: new claim overlaps with ancestor '{cycle_ancestor}'"

        return True, ""

    def _detect_cycle(self, new_claim: str) -> Optional[str]:
        """Check if a new claim overlaps too much with any existing claim.

        Uses word-overlap ratio: if >60% of the words in the new claim
        appear in an ancestor claim, it's likely a cycle.

        Args:
            new_claim: The proposed claim text for the next depth level.

        Returns:
            The overlapping ancestor's claim string, or None if no cycle.
        """
        new_words = set(new_claim.lower().split())
        if not new_words:
            return None

        for h in self.hypotheses:
            ancestor_words = set(h.claim.lower().split())
            if not ancestor_words:
                continue
            overlap = len(new_words & ancestor_words) / len(new_words)
            if overlap > self.CYCLE_WORD_OVERLAP:
                return h.claim

        return None

    def check_confidence_termination(self, hypothesis: Hypothesis) -> tuple[bool, str]:
        """Check if a verified hypothesis's confidence is too low to continue.

        Called after verification. If confidence < MIN_CONFIDENCE, the chain
        should stop — the deeper cause is too speculative.

        Returns:
            (should_terminate, reason)
        """
        if hypothesis.confidence < self.MIN_CONFIDENCE:
            return True, f"Confidence too low ({hypothesis.confidence} < {self.MIN_CONFIDENCE})"
        return False, ""

    def to_prompt_summary(self) -> str:
        """Render the chain as a readable summary for prompts."""
        path = self.get_chain_path()
        if not path:
            return "No confirmed causes yet."

        lines = []
        for i, h in enumerate(path):
            indent = "  " * i
            arrow = "→ " if i > 0 else ""
            lines.append(f"{indent}{arrow}**{h.claim}** [{h.category}] (confidence: {h.confidence})")
            if h.suspected_file:
                lines.append(f"{indent}  File: `{h.suspected_file}`")
            if h.evidence:
                supporting = [e for e in h.evidence if e.supports]
                if supporting:
                    lines.append(f"{indent}  Evidence: {supporting[0].reasoning}")

        return "\n".join(lines)
