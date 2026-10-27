"""Exposed-file and directory-listing checks: pure functions over a Response (no network)."""
import re

from src.evidence import redact_text
from src.models import Finding
from src.scanner.findings import from_response

ENV_LINE = re.compile(rb"(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*\s*=")
GIT_HEAD = re.compile(rb"^(?:ref: refs/\S+|[0-9a-f]{40})\s*$")
LISTING = re.compile(rb"(?i)<title>\s*(?:index of /|directory listing for /)")
EXCERPT_CHARS = 200


def _ok(resp) -> bool:
    return resp.status == 200 and bool(resp.body)


def _is_html(resp) -> bool:
    return "html" in (resp.header("content-type") or "").lower()


def _excerpt(resp) -> str:
    # redacted, so a secret-looking value in the body never lands in a Finding either
    return redact_text(resp.body[:EXCERPT_CHARS].decode("utf-8", "replace"))


def check_env_file(resp) -> list[Finding]:
    if _ok(resp) and not _is_html(resp) and ENV_LINE.search(resp.body):
        return [from_response(resp, "exposed_env_file", _excerpt(resp),
                              f"HTTP 200 with KEY=value lines at {resp.url}.")]
    return []


def check_git_head(resp) -> list[Finding]:
    if _ok(resp) and GIT_HEAD.match(resp.body.strip()):
        return [from_response(resp, "exposed_git_dir", _excerpt(resp), f"Git HEAD file is readable at {resp.url}.")]
    return []


def check_backup_file(resp) -> list[Finding]:
    # HTML 200s are usually a catch-all page, not the file
    if _ok(resp) and not _is_html(resp):
        return [from_response(resp, "exposed_backup_file", _excerpt(resp),
                              f"HTTP 200 ({len(resp.body)}+ bytes, {resp.header('content-type')}) at {resp.url}.")]
    return []


def check_directory_listing(resp) -> list[Finding]:
    if _ok(resp) and LISTING.search(resp.body):
        return [from_response(resp, "directory_listing", _excerpt(resp), f"Directory index page at {resp.url}.")]
    return []
