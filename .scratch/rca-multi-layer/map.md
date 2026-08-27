# Wayfinder Map: Multi-Layer RCA Architecture

Label: wayfinder:map

## Destination

Design a hypothesis-test loop with causal chaining architecture for `RCAAgent`, replacing the single Gemini call with an iterative agent that:

1. Generates 2-3 hypotheses from initial evidence
2. Gathers targeted evidence per hypothesis (specific code searches, narrower log queries)
3. Verifies/refutes each hypothesis against evidence
4. For the surviving hypothesis, asks "but WHY?" and loops (up to 3 levels)
5. Produces a final RCA with a full causal chain, grounded in verified code/log references

## Notes

- Domain: Solargraf platform RCA bot (Python, FastAPI, Gemini, ChromaDB, SigNoz)
- Keep existing infrastructure (SigNoz, ChromaDB, Gemini) — this is an architectural refactor of `rca_agent.py` and `prompts.py`, not a rewrite of services
- Skills to consult: `/codebase-design`, `/grilling`

## Decisions so far

- [What is a "hypothesis"?](issues/01-what-is-a-hypothesis.md) — A dataclass with claim, category, suspected_file/function, testable predictions (search_type + query + expected), evidence list, confidence score, and parent_id for causal chaining. Implemented in `agents/hypothesis.py`.
- [Targeted evidence gathering](issues/02-targeted-evidence-gathering.md) — Gemini generates predictions, our code executes them. Three search types: `code_search`, `log_query` (new `fetch_logs_by_expression`), `log_query_api`. Gatherer collects unjudged evidence; verifier judges later. Implemented in `agents/evidence_gatherer.py`.
- [Verification mechanism](issues/03-verification-mechanism.md) — Hybrid-lightweight: 3 rule-based pre-checks (no-results, file-exists, error-severity), then one Gemini call per hypothesis for batch judgment. Returns verdict + follow_up_question for chaining. Inconclusive = demote. Implemented in `agents/verifier.py`.
- [Causal chain depth](issues/04-causal-chain-depth.md) — Max depth=2, 3 termination conditions (no follow-up, evidence drought, confidence<0.4), word-overlap cycle detection, single chain no branching. Implemented in `CausalChain` in `agents/hypothesis.py`.
- [Output format](issues/05-output-format.md) — Flat numbered chain (H3 per layer), dual output (Markdown for Teams via Power Automate + JSON for polling API), backward compatible header. Implemented in `agents/output_renderer.py`.
- [Agent loop code structure](issues/06-agent-loop-code-structure.md) — Simple async while-loop in `HypothesisLoop.run()`. Max 10 Gemini calls. Malformed JSON = refute hypothesis. No state persistence. Drop-in compatible. Implemented in `agents/hypothesis_loop.py`.

## Not yet specified

- **Evaluation harness** — once the architecture is decided, a test harness with ground-truth cases to measure whether the new pipeline actually produces better RCAs. Depends on output format (ticket 05) and loop structure (ticket 06).
- **Prompt engineering for each loop stage** — the specific prompts for hypothesis generation, evidence evaluation, and chain-deepening. Depends on decisions from tickets 01-04.
- **Cost/latency budget** — how many Gemini calls per analysis is acceptable? Currently 2-3. The loop will increase this. Needs real numbers from Gemini pricing.

## Out of scope

- Replacing Gemini with another LLM provider
- Changing the SigNoz or ChromaDB infrastructure
- Frontend/Teams bot UI changes (beyond output format)
- Multi-agent orchestration framework (e.g. LangGraph, CrewAI) — keep this as plain Python
