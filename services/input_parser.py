"""
Input Parser — extracts projectId, proposalId, and issue description
from a Solargraf app URL + user query.

Supported URL formats:
  - https://app.solargraf.com/projects/342321
  - https://app.solargraf.com/projects/342321/proposals/abc123
  - https://develop.solargraf.com/projects/342321/proposals/abc123/...
  - /projects/342321/proposals/abc123  (bare path)
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


# Regex to extract projectId and optional proposalId from the URL path
_PROJECT_RE = re.compile(r"/projects/([^/]+)")
_PROPOSAL_RE = re.compile(r"/proposals/([^/]+)")


@dataclass
class AnalysisRequest:
    """Structured representation of a user's incident analysis request."""

    project_id: str
    issue_description: str
    proposal_id: Optional[str] = None
    raw_url: str = ""
    url_path: str = ""


_URL_RE = re.compile(r"https?://[^\s]+solargraf\.com[^\s]*")


def _url_to_path(url: str) -> str:
    """Convert a full URL or bare path to just the path component."""
    if url.startswith("http://") or url.startswith("https://"):
        return urlparse(url).path
    return url


def _strip_urls(text: str) -> str:
    """Remove all solargraf URLs from text and collapse extra whitespace."""
    cleaned = _URL_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def parse_input(url: str, query: str) -> AnalysisRequest:
    """
    Parse a Solargraf app URL and issue description into an AnalysisRequest.

    Also extracts URLs embedded in the query text. The most specific
    (longest) path is used for url_path, and URLs are stripped from
    the issue_description to keep keyword extraction clean.

    Args:
        url: Solargraf app URL or bare path containing projectId.
        query: User's issue description (may also contain URLs).

    Returns:
        AnalysisRequest with extracted IDs and the issue description.

    Raises:
        ValueError: If projectId cannot be extracted from any URL.
    """
    # Collect all candidate paths: from the explicit url param and from the query
    paths: list[str] = []
    if url:
        paths.append(_url_to_path(url))

    for match in _URL_RE.finditer(query):
        matched_url = match.group(0).rstrip(".,;:!?")
        paths.append(_url_to_path(matched_url))

    if not paths:
        raise ValueError(
            f"No Solargraf URL found in url or query. "
            "Expected format: https://app.solargraf.com/projects/<projectId>"
        )

    # Pick the most specific path (longest, has the most segments)
    best_path = max(paths, key=len)

    # Extract projectId
    project_match = _PROJECT_RE.search(best_path)
    if not project_match:
        raise ValueError(
            f"Could not extract projectId from URL: {url}. "
            "Expected format: https://app.solargraf.com/projects/<projectId>"
        )
    project_id = project_match.group(1)

    # Extract optional proposalId
    proposal_match = _PROPOSAL_RE.search(best_path)
    proposal_id = proposal_match.group(1) if proposal_match else None

    # Clean URLs out of the issue description
    clean_query = _strip_urls(query)

    return AnalysisRequest(
        project_id=project_id,
        proposal_id=proposal_id,
        issue_description=clean_query,
        raw_url=url,
        url_path=best_path,
    )


def extract_url_from_message(message: str) -> tuple[Optional[str], str]:
    """
    Extract a Solargraf URL from a Teams/chat message and return
    the URL and the remaining text (issue description).

    Args:
        message: Raw user message that may contain a URL.

    Returns:
        Tuple of (url_or_None, remaining_text).
    """
    # Match full URLs
    url_pattern = re.compile(
        r"(https?://[^\s]+solargraf\.com[^\s]*)"
    )
    match = url_pattern.search(message)

    if match:
        url = match.group(1).rstrip(".,;:!?")
        remaining = message[:match.start()] + message[match.end():]
        return url, remaining.strip()

    # Match bare paths like /projects/12345/...
    path_pattern = re.compile(r"(/projects/[^\s]+)")
    match = path_pattern.search(message)

    if match:
        path = match.group(1).rstrip(".,;:!?")
        remaining = message[:match.start()] + message[match.end():]
        return path, remaining.strip()

    return None, message.strip()
