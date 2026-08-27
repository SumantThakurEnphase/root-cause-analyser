"""
Output Renderer — converts a CausalChain into human-readable Markdown
and machine-readable JSON for the RCA pipeline output.

Design constraints:
- Markdown renders in Microsoft Teams via Power Automate + Cloudflare tunnel
- Teams Markdown quirks: no deep nesting, limited heading levels, basic code blocks
- JSON used by polling API (/api/analyze/status/) and potentially Adaptive Cards
- Backward compatible: starts with ## 🔍 Root Cause Analysis header
"""

from __future__ import annotations

from typing import Any

from agents.hypothesis import CausalChain, Hypothesis, HypothesisStatus


class OutputRenderer:
    """Renders CausalChain into Markdown and JSON formats."""

    def render_markdown(self, chain: CausalChain, query: str = "") -> str:
        """Render a CausalChain as Teams-compatible Markdown.

        Args:
            chain: The completed causal chain with confirmed hypotheses.
            query: The original user query/issue description.

        Returns:
            Markdown string ready for Teams display.
        """
        path = chain.get_chain_path()
        if not path:
            return self._render_empty(query)

        lines = []

        # Header — backward compatible with existing format
        lines.append("## 🔍 Root Cause Analysis\n")

        # Category from the top-level hypothesis
        top = path[0]
        category_label = top.category.replace("_", " ").title()
        lines.append(f"**Cause Category:** {category_label}\n")

        # Chain summary — quick visual of the causal path
        chain_labels = [self._short_label(h, i) for i, h in enumerate(path)]
        lines.append("**Causal Chain:** " + " → ".join(chain_labels) + "\n")
        lines.append("---\n")

        # Each layer as a flat H3 section
        for i, h in enumerate(path):
            layer_label = self._layer_label(i, len(path))
            lines.append(f"### 🔗 {layer_label}: {h.claim}\n")

            # Confidence
            conf_label = self._confidence_label(h.confidence)
            lines.append(f"**Confidence:** {conf_label} ({h.confidence:.0%})\n")

            # Affected file
            if h.suspected_file:
                fn = f" — `{h.suspected_function}`" if h.suspected_function else ""
                lines.append(f"**Affected File:** `{h.suspected_file}`{fn}\n")

            # Evidence
            supporting = [e for e in h.evidence if e.supports is True]
            if supporting:
                lines.append("**Evidence:**")
                for e in supporting[:3]:  # Cap at 3 per layer for readability
                    ref = e.reference
                    reasoning = e.reasoning or ""
                    if e.source == "code":
                        lines.append(f"- 📄 `{ref}` — {reasoning}")
                    elif e.source == "log":
                        lines.append(f"- 📋 Log `{ref}` — {reasoning}")
                    else:
                        lines.append(f"- {ref} — {reasoning}")
                lines.append("")

            # Why? connector to next layer
            if i < len(path) - 1:
                lines.append(f"**↓ Why?** _{path[i + 1].claim}_\n")

        lines.append("---\n")

        # Root cause summary + suggested fix
        root = chain.root_cause or path[-1]
        lines.append("### 🎯 Root Cause Summary\n")
        lines.append(f"{root.claim}\n")
        if root.suspected_file:
            fn = f"::{root.suspected_function}" if root.suspected_function else ""
            lines.append(f"**File:** `{root.suspected_file}{fn}`\n")

        # Confidence summary
        avg_conf = sum(h.confidence for h in path) / len(path)
        lines.append(f"**Overall Confidence:** {self._confidence_label(avg_conf)} ({avg_conf:.0%})\n")

        # Other hypotheses considered (refuted ones)
        refuted = [h for h in chain.hypotheses
                   if h.status == HypothesisStatus.REFUTED and h.depth == 0]
        if refuted:
            lines.append("**Other hypotheses considered (refuted):**")
            for h in refuted[:3]:
                lines.append(f"- ~~{h.claim}~~ — {h.confidence:.0%} confidence")
            lines.append("")

        return "\n".join(lines)

    def render_json(self, chain: CausalChain, query: str = "") -> dict[str, Any]:
        """Render a CausalChain as structured JSON for the polling API.

        Returns a dict with:
        - query: original issue
        - category: top-level cause category
        - chain: list of chain nodes (shallowest to deepest)
        - root_cause: the deepest confirmed node
        - refuted_hypotheses: list of top-level hypotheses that were rejected
        """
        path = chain.get_chain_path()
        if not path:
            return {
                "query": query,
                "category": "unknown",
                "chain": [],
                "root_cause": None,
                "refuted_hypotheses": [],
            }

        chain_nodes = []
        for i, h in enumerate(path):
            node = {
                "depth": h.depth,
                "layer": self._layer_label(i, len(path)),
                "category": h.category,
                "claim": h.claim,
                "confidence": h.confidence,
                "suspected_file": h.suspected_file or None,
                "suspected_function": h.suspected_function or None,
                "evidence": [
                    {
                        "source": e.source,
                        "reference": e.reference,
                        "supports": e.supports,
                        "reasoning": e.reasoning,
                    }
                    for e in h.evidence
                    if e.supports is True
                ],
            }
            chain_nodes.append(node)

        root = chain.root_cause or path[-1]

        refuted = [
            {"claim": h.claim, "confidence": h.confidence, "category": h.category}
            for h in chain.hypotheses
            if h.status == HypothesisStatus.REFUTED and h.depth == 0
        ]

        return {
            "query": query,
            "category": path[0].category,
            "chain": chain_nodes,
            "root_cause": {
                "claim": root.claim,
                "confidence": root.confidence,
                "suspected_file": root.suspected_file or None,
                "suspected_function": root.suspected_function or None,
            },
            "refuted_hypotheses": refuted[:3],
        }

    @staticmethod
    def _render_empty(query: str) -> str:
        """Render output when no causal chain was established."""
        return (
            "## 🔍 Root Cause Analysis\n\n"
            "**Cause Category:** Unknown\n\n"
            f"Unable to establish a causal chain for: _{query}_\n\n"
            "The analysis could not confirm any hypothesis with sufficient confidence. "
            "This may indicate insufficient log data, unindexed code, or a novel issue pattern.\n"
        )

    @staticmethod
    def _layer_label(index: int, total: int) -> str:
        """Generate a human label for a chain layer."""
        if total == 1:
            return "Root Cause"
        if index == 0:
            return "Symptom"
        if index == total - 1:
            return "Root Cause"
        return f"Cause Layer {index}"

    @staticmethod
    def _short_label(h: Hypothesis, index: int) -> str:
        """Short label for the chain summary line."""
        claim = h.claim
        if len(claim) > 60:
            claim = claim[:57] + "..."
        return claim

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        """Map confidence score to a human label."""
        if confidence >= 0.8:
            return "High"
        if confidence >= 0.5:
            return "Medium"
        return "Low"
