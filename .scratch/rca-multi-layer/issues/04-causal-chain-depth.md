# 04 — How deep should causal chaining go, and what stops infinite loops?

Type: grilling
Status: resolved
Blocked by: 02, 03

## Question

After a hypothesis is confirmed (e.g. "eligibility check uses `.length`"), the system asks "WHY does it use `.length`?" to trace the causal chain deeper. Define:

1. **Max depth** — 2 levels? 3? Should it be configurable per cause category?
2. **Termination conditions** — When does the chain stop? Options:
   - Hit a code-level leaf (a specific line in a specific file)
   - No further evidence available from logs or code
   - The "why" answer is a human decision ("developer wrote it that way")
   - Confidence drops below threshold
3. **Cycle detection** — How to detect when the chain loops back to something already investigated?
4. **Branch handling** — What if a cause has multiple sub-causes? (e.g. "data issue" caused by both "missing validation" AND "race condition")

### Context

The error_analysis.md example shows the current shallow depth: it says "Code Bug" and guesses at the file, but doesn't trace WHY the code is wrong (was it a regression? a misunderstanding of the data model? a copy-paste from another function?). The chain should go deep enough to be actionable but not so deep it wastes tokens on philosophy.

## Answer

Four decisions made:

1. **Max depth = 2**, not configurable per category. Depth 0 = "what went wrong?", depth 1 = "why?", depth 2 = "why why?". Gets to actionable root cause. Total Gemini budget ~8-10 calls.

2. **Three early termination conditions** (checked in order):
   - No follow-up question from verifier (Gemini decided nothing deeper to chase)
   - Evidence drought (gatherer returned all "no results" items)
   - Confidence < 0.4 (chain getting speculative)

3. **Cycle detection via word overlap** — before creating a child hypothesis, compare its claim against all ancestor claims. If >60% of words overlap, it's a cycle — terminate. Lightweight enough for depth=2.

4. **Single chain, no branching** — multiple root causes handled by competing top-level hypotheses (2-3 generated initially), not by branching within a chain. Keeps Gemini budget and output format simple.

Implemented in `agents/hypothesis.py`:

- `CausalChain.MAX_DEPTH = 2`, `MIN_CONFIDENCE = 0.4`, `CYCLE_WORD_OVERLAP = 0.6`
- `should_deepen(follow_up_question, child_claim)` — checks all three termination conditions
- `_detect_cycle(new_claim)` — word-overlap cycle detection
- `check_confidence_termination(hypothesis)` — post-verification confidence check
