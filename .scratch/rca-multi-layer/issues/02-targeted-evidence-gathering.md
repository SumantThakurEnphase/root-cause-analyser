# 02 — How should evidence gathering be targeted per hypothesis?

Type: grilling
Status: resolved
Blocked by: 01

## Question

Currently, all log/code gathering happens upfront (steps 2-3 in `_analyze_with_url`). In the new loop, the agent needs to gather _different_ evidence depending on the hypothesis. What tools/queries should each hypothesis type trigger?

### Context

Examples of targeted gathering:

- Hypothesis: "missing await on async function" → search ChromaDB for the function signature, check if it's `async`, look for callers that don't `await`
- Hypothesis: "data issue — record not created" → query SigNoz for the write operation (POST/PUT) that should have created the data, check if it returned success
- Hypothesis: "wrong filtering logic" → search for the filter function in code, look for `.length` vs `.size` patterns

Key design tension: the agent needs to _generate_ the right query/search for each hypothesis. This is either (a) prompt-driven (tell Gemini what to search for), (b) rule-based (map hypothesis categories to search strategies), or (c) tool-use (give Gemini access to search/log tools and let it decide).

## Answer

Four decisions made:

1. **Gemini generates the predictions** — no hardcoded strategies per category. Gemini produces `TestPrediction` objects (search_type + query + expected) as part of hypothesis generation. Our code validates and executes.

2. **Added `fetch_logs_by_expression(expression)`** to `SigNozClient` — a thin wrapper around `_build_signoz_payload` + `_call_signoz` that accepts raw SigNoz filter expressions (e.g. `severity_text = 'error' AND endpoint CONTAINS '/financing'`). Gives Gemini precise log targeting.

3. **Three search types**, each mapping to one service method:
   - `code_search` → `CodeSearchService.search(query)`
   - `log_query` → `SigNozClient.fetch_logs_by_expression(expression)`
   - `log_query_api` → `SigNozClient.fetch_logs_by_api(api_path, project_id)`
   - Dropped `file_exists` (subsumed by `code_search` + post-check)

4. **Gatherer collects, verifier judges** — `EvidenceItem.supports` changed from `bool` to `Optional[bool]`. The gatherer produces unjudged evidence (supports=None); the verifier (ticket 03) assigns supports/refutes.

Implemented in:

- `agents/evidence_gatherer.py` — `EvidenceGatherer` class with `gather(hypothesis)` method
- `services/signoz_client.py` — added `fetch_logs_by_expression()`
- `agents/hypothesis.py` — updated `EvidenceItem.supports` to `Optional[bool]`, updated `search_type` values, fixed `add_evidence()` to skip unjudged items in confidence calc
