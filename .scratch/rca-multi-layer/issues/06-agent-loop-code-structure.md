# 06 — How should the new agent loop be structured in code?

Type: grilling
Status: resolved
Blocked by: 02, 03, 04

## Question

Design the control flow for the new `RCAAgent.analyze()`. Key decisions:

1. **Loop structure** — Synchronous loop (`while not done`) vs async state machine? The current code runs in a background thread via `_run_analysis_sync`. Does the new loop fit this model or need something different?

2. **Gemini call budget** — How many Gemini calls per analysis is acceptable? Currently 2-3 (classify + validate intent + final). The hypothesis-test loop will add: hypothesis generation (1), evidence evaluation per hypothesis (2-3), chain-deepening per level (1-3). That's potentially 7-10 calls. Is this acceptable for latency and cost?

3. **Failure handling** — What happens when Gemini returns malformed JSON mid-loop? Retry? Skip that hypothesis? Fall back to the current single-shot pipeline?

4. **Intermediate state** — Should intermediate state (hypotheses, evidence, partial chains) be persisted? Useful for debugging and for the polling API to show progress, but adds complexity.

5. **Backward compatibility** — The current `_analyze_with_url` and `_analyze_query_only` methods are called from `bot.py` and `main.py`. The new loop should be a drop-in replacement (same interface, richer output).

### Context

The current `_analyze_with_url` in `rca_agent.py` (lines 71-147) is a linear 7-step pipeline. The new design replaces steps 4-7 with the hypothesis-test loop while keeping steps 1-3 (parse URL, discover APIs, fetch initial logs) as the evidence bootstrapping phase.

## Answer

Five decisions made:

1. **Simple async while-loop** — No state machine. `HypothesisLoop.run()` is an async method that runs inside the existing `_run_analysis_sync` background thread. Three phases: generate hypotheses → test each (gather + verify) → deepen the best one.

2. **Max 10 Gemini calls** — Hard cap enforced via `_gemini_call_count`. Breakdown: 1 hypothesis generation + up to 3 verifications + up to 2 chain-deepening (generation + verification per depth). Budget checked before every call.

3. **Failure handling** — Malformed JSON mid-loop → mark that hypothesis as REFUTED, continue to next. If ALL hypotheses fail → the renderer produces a "could not establish causal chain" output. No fallback to old pipeline (old pipeline is still available as `_analyze_query_only` for URL-less queries).

4. **No intermediate state persistence** — Too complex for the gain. Debugging via existing `_dump_prompt`/`_dump_logs` pattern + console print statements at each loop step.

5. **Backward compatible interface** — `HypothesisLoop.run()` returns `(markdown_string, json_dict)`. The calling code in `rca_agent.py` uses the markdown string (same as before). JSON dict available for the analysis store / polling API.

Implemented in:

- `agents/hypothesis_loop.py` — `HypothesisLoop` class with `run()`, `_generate_hypotheses()`, `_verify_with_budget()`, `_deepen_chain()`, `_generate_child_hypothesis()`
- Prompts: `HYPOTHESIS_GENERATION_PROMPT`, `CHAIN_DEEPENING_PROMPT`
- Integration point: `rca_agent.py` `_analyze_with_url` steps 1-3 remain, steps 4-7 replaced by `HypothesisLoop.run()`
