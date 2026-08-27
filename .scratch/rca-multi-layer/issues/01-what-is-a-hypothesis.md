# 01 — What is a "hypothesis" in this system?

Type: grilling
Status: resolved
Blocked by: (none)

## Question

Define the data structure for a hypothesis. What fields does it need? (e.g. category, suspected file, suspected function, causal claim, evidence pointers, confidence). How does a hypothesis differ from the current `CauseCategory` classification? Should a hypothesis include a testable prediction ("if this is right, I should find X in the logs")?

### Context

Currently, the pipeline classifies into one of 7 `CauseCategory` values (`code_bug`, `config_issue`, etc.) via a single Gemini call in `_classify_cause()`. This produces a label but no reasoning structure. A hypothesis needs to be richer — it should encode a _claim_ about what went wrong and _how to verify it_, not just a category tag.

Key design tension: too simple (just a string) and the loop can't reason about it; too complex (deeply nested object) and the prompts become unwieldy and Gemini struggles to produce valid structured output.

## Answer

Implemented in `agents/hypothesis.py`. The data model has four classes:

1. **`Hypothesis`** — The core claim. Fields:
   - `category` (reuses CauseCategory values for compatibility)
   - `claim` (natural language: "The eligibility check counts panel rows instead of unique module types")
   - `suspected_file`, `suspected_function`, `suspected_line` (optional specifics)
   - `predictions: list[TestPrediction]` (testable claims that drive evidence gathering)
   - `evidence: list[EvidenceItem]` (collected during investigation)
   - `status` (proposed → investigating → supported/refuted → confirmed)
   - `confidence` (0.0-1.0, auto-updated from evidence ratio)
   - `parent_id` + `depth` (for causal chaining: "this hypothesis explains WHY the parent happened")

2. **`TestPrediction`** — A testable statement: "If this hypothesis is correct, I should find X when I search Y."
   - `search_type`: `code_search`, `log_query`, or `file_exists`
   - `query`: the actual search to run
   - `expected`: what we expect to find

3. **`EvidenceItem`** — A piece of evidence (log line, code snippet) with a `supports: bool` flag and reasoning.

4. **`CausalChain`** — A container for linked hypotheses, with helpers to get the chain path and render it for prompts.

**Key design decisions:**

- Hypothesis is a dataclass (not Pydantic) to match existing codebase style
- `from_gemini_dict()` factory handles parsing Gemini's JSON output with graceful defaults
- `to_prompt_dict()` serializes only what the LLM needs (omits internal tracking fields)
- Confidence is a simple supporting/total ratio — good enough for ranking, no over-engineering
- Predictions are the bridge between "what do we think went wrong" and "what should we search for next"
