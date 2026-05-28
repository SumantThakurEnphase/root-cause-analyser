"""
Cause Categories — defines incident cause types and the classifier prompt
for the two-phase analysis pipeline.
"""

from enum import Enum


class CauseCategory(str, Enum):
    """Classification of incident root cause types."""

    CODE_BUG = "code_bug"
    CONFIG_ISSUE = "config_issue"
    INFRASTRUCTURE = "infrastructure"
    THIRD_PARTY_API = "third_party_api"
    DATA_ISSUE = "data_issue"
    UNKNOWN = "unknown"


# Human-readable labels for each category
CATEGORY_LABELS = {
    CauseCategory.CODE_BUG: "Code Bug",
    CauseCategory.CONFIG_ISSUE: "Configuration Issue",
    CauseCategory.INFRASTRUCTURE: "Infrastructure / Deployment",
    CauseCategory.THIRD_PARTY_API: "Third-Party / External API",
    CauseCategory.DATA_ISSUE: "Data Issue",
    CauseCategory.UNKNOWN: "Unknown / Needs More Info",
}

# Descriptions for the classifier prompt
CATEGORY_DESCRIPTIONS = {
    CauseCategory.CODE_BUG: (
        "A bug in the application source code: null pointer, missing await, "
        "type error, logic error, missing return, incorrect variable reference, etc."
    ),
    CauseCategory.CONFIG_ISSUE: (
        "A configuration or feature flag problem: feature toggle disabled, "
        "env var mismatch, service config drift, wrong URL/endpoint configured, "
        "feature not visible due to company/user flag settings."
    ),
    CauseCategory.INFRASTRUCTURE: (
        "An infrastructure or deployment issue: pod crash/restart, OOM kill, "
        "deploy failure, scaling problem, network timeout between services, "
        "DNS resolution failure, certificate expiry."
    ),
    CauseCategory.THIRD_PARTY_API: (
        "A failure in an external/third-party service: Genability API error, "
        "Puppeteer/Chromium not installed, Google Maps API failure, "
        "payment gateway timeout, external webhook failure."
    ),
    CauseCategory.DATA_ISSUE: (
        "A data integrity or data availability problem: missing DB records, "
        "corrupt data from migration, null geometry in MongoDB, "
        "empty API response leading to bad calculations, stale cache."
    ),
    CauseCategory.UNKNOWN: (
        "Cannot confidently determine the cause from available information. "
        "More data, logs, or context is needed."
    ),
}


CLASSIFIER_PROMPT = """You are an incident cause classifier for the Solargraf platform.

Given an issue description and log entries, classify the root cause into exactly ONE category.

## Categories

{categories}

## Instructions

1. Read the issue description and logs carefully.
2. Look for the PRIMARY cause, not secondary symptoms.
3. If logs show an error in code (TypeError, missing await, null reference), classify as "code_bug".
4. If logs mention feature flags, config, toggles, or a feature not being visible, classify as "config_issue".
5. If logs show pod restarts, OOM, deploy errors, or infra-level issues, classify as "infrastructure".
6. If logs show a third-party service failure (Genability, Puppeteer, external API), classify as "third_party_api".
7. If logs show missing/corrupt data, empty DB results, or data migration issues, classify as "data_issue".
8. If you're unsure, classify as "unknown".

Respond ONLY with a JSON object:
{{
  "category": "<one of: code_bug, config_issue, infrastructure, third_party_api, data_issue, unknown>",
  "confidence": "<high, medium, or low>",
  "reasoning": "<1-2 sentence explanation>"
}}
"""


def build_classifier_prompt() -> str:
    """Build the classifier prompt with all category descriptions."""
    category_lines = []
    for cat, desc in CATEGORY_DESCRIPTIONS.items():
        label = CATEGORY_LABELS[cat]
        category_lines.append(f"- **{label}** (`{cat.value}`): {desc}")

    categories_text = "\n".join(category_lines)
    return CLASSIFIER_PROMPT.format(categories=categories_text)
