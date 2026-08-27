# 03 — How should verification work — LLM-as-judge or rule-based?

Type: grilling
Status: resolved
Blocked by: 01

## Question

After gathering targeted evidence for a hypothesis, how does the system decide the hypothesis is confirmed or refuted?

### Options

**(a) LLM-as-judge** — Another Gemini call with a structured prompt: "Given this hypothesis and this evidence, is the hypothesis supported? Rate confidence."

- Pro: Flexible, handles nuance
- Con: Extra API call per hypothesis, Gemini may be inconsistent

**(b) Rule-based checks** — Programmatic verification:

- "File X exists in ChromaDB index" = partial confirmation
- "Function Y is async" = confirmed (if hypothesis was about missing await)
- "Log shows 4xx at endpoint Z" = confirmed (if hypothesis was about API failure)
- Pro: Fast, deterministic, no extra API cost
- Con: Limited coverage, can't handle novel patterns

**(c) Hybrid** — Rule-based checks first (cheap), then LLM-as-judge for what rules can't cover

- Pro: Best of both
- Con: More complex to implement

### Context

The current pipeline has zero verification — Gemini's output is taken as-is. Even basic checks like "does the file Gemini mentioned actually exist in our codebase index?" would be a major improvement.

## Answer

Three decisions made:

1. **Hybrid-lightweight** — Three cheap rule-based pre-checks run before any LLM call:
   - "No results" check: empty evidence (no code/logs found) = weak refutation
   - "File/function exists" check: suspected file found in code search = partial support
   - "Error severity" check: log query expecting errors got only info-level = prediction failed
     Then one Gemini call per hypothesis judges remaining unjudged evidence in a batch.

2. **Verification + chaining in one call** — Gemini returns a JSON verdict with `verdict`, `confidence`, `reasoning`, per-evidence `evidence_judgments`, and a `follow_up_question` that seeds the next depth level of causal chaining. No separate chaining call needed.

3. **Inconclusive = demote** — If Gemini returns `inconclusive`, the hypothesis is marked `REFUTED` and skipped. No retry round — spend the budget on other hypotheses or deeper chaining on the supported one.

Implemented in:

- `agents/verifier.py` — `EvidenceVerifier` class with `verify(hypothesis)` method and `VerificationResult` data class
- Rule checks in `_apply_rule_checks()`, LLM verification in `_llm_verify()`
- Prompt template `VERIFICATION_PROMPT` asks Gemini for verdict + evidence judgments + follow_up_question
