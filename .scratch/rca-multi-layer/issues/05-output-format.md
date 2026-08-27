# 05 — What should the output format look like for causal chains?

Type: grilling
Status: resolved
Blocked by: 04

## Question

The current `_RESPONSE_FORMAT` in `prompts.py` is flat (Error 1, Error 2). A causal chain is a linked list of causes. Design the output format.

### Considerations

1. **Structure** — Should it be:
   - A numbered chain: `Symptom → Cause 1 → Cause 2 → Root Cause`
   - A tree (for branching causes)
   - Nested sections in Markdown

2. **Per-node content** — Each link in the chain needs:
   - What happened (the claim)
   - Evidence (log entry or code snippet that proves it)
   - Confidence for this specific link

3. **Teams compatibility** — The output renders in Microsoft Teams via Bot Framework. Teams supports Markdown but has rendering quirks. Deeply nested structures may not render well.

4. **Machine-readability** — Should the output also include a structured JSON representation (for the polling API at `/api/analyze/status/`) alongside the human-readable Markdown?

### Context

Current format from `prompts.py` lines 34-68 uses flat sections: Root Cause, Affected Files, Evidence, Suggested Fix. The new format needs to show the _journey_ from symptom to root cause, not just the destination.

## Answer

Four decisions made (note: delivery is via Power Automate + Cloudflare tunnel to Teams):

1. **Flat numbered chain** — Each layer is an H3 section (`### 🔗 Symptom`, `### 🔗 Cause Layer 1`, `### 🔗 Root Cause`). No deep nesting — Teams-compatible. Layers connected with `↓ Why?` connectors.

2. **Per-node content** — Each layer has: claim, confidence (label + percentage), affected file/function, up to 3 supporting evidence items with source icons (📄 code, 📋 log).

3. **Dual output** — `render_markdown()` for Teams display (backward compatible `## 🔍 Root Cause Analysis` header), `render_json()` for polling API with structured chain/root_cause/refuted_hypotheses. Power Automate can use either.

4. **Backward compatible** — Same header, same Cause Category line. Existing Power Automate flows won't break. Refuted hypotheses shown at the bottom as strikethrough.

Implemented in:

- `agents/output_renderer.py` — `OutputRenderer` class with `render_markdown()` and `render_json()` methods
