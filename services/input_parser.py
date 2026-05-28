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


def parse_input(url: str, query: str) -> AnalysisRequest:
    """
    Parse a Solargraf app URL and issue description into an AnalysisRequest.

    Args:
        url: Solargraf app URL or bare path containing projectId.
        query: User's issue description (e.g., "roofline detection not working").

    Returns:
        AnalysisRequest with extracted IDs and the issue description.

    Raises:
        ValueError: If projectId cannot be extracted from the URL.
    """
    # Normalize: if it looks like a full URL, parse it; otherwise treat as bare path
    if url.startswith("http://") or url.startswith("https://"):
        parsed = urlparse(url)
        path = parsed.path
    else:
        path = url

    # Extract projectId
    project_match = _PROJECT_RE.search(path)
    if not project_match:
        raise ValueError(
            f"Could not extract projectId from URL: {url}. "
            "Expected format: https://app.solargraf.com/projects/<projectId>"
        )
    project_id = project_match.group(1)

    # Extract optional proposalId
    proposal_match = _PROPOSAL_RE.search(path)
    proposal_id = proposal_match.group(1) if proposal_match else None

    return AnalysisRequest(
        project_id=project_id,
        proposal_id=proposal_id,
        issue_description=query.strip(),
        raw_url=url,
        url_path=path,
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
