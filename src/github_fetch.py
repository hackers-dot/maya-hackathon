"""GitHub URL connector: public repositories only, no token, read-only.

Only https://github.com/<owner>/<repo>[/tree/<branch>] is accepted (strict regex; no userinfo, port,
query, fragment or other host). The repository ZIP is fetched from codeload.github.com with GET, a 5 s connect /
30 s total timeout, redirects followed only to codeload.github.com, and a 25 MB cap (reading stops when it is
exceeded). The bytes go straight to the in-memory code-ZIP scanner (src/code_scan.py): nothing is written to
disk, extracted or executed. The repository name is sent to GitHub (the UI says so).
"""
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 25 * 1024 * 1024
CONNECT_TIMEOUT_S = 5.0
TOTAL_TIMEOUT_S = 30.0
MAX_REDIRECTS = 3
ZIP_HOST = "codeload.github.com"
OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}"   # GitHub user/org name rules
REPO = r"[A-Za-z0-9_.-]{1,100}"
BRANCH = r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,99}"
URL = re.compile(rf"^https://github\.com/(?P<owner>{OWNER})/(?P<repo>{REPO})(?:/tree/(?P<branch>{BRANCH}))?/?$")


class GitHubError(ValueError):
    pass


def parse_github_url(url) -> tuple[str, str, str | None]:
    """(owner, repo, branch or None); raises GitHubError for anything that is not a plain public-repo URL."""
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in url):
        raise GitHubError("enter a GitHub address like https://github.com/owner/repo")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment or "\\" in url:
        raise GitHubError("only https://github.com/<owner>/<repo> addresses are accepted")
    m = URL.match(url)
    if not m:
        raise GitHubError("only https://github.com/<owner>/<repo> or .../tree/<branch> addresses are accepted")
    owner, repo, branch = m["owner"], m["repo"].removesuffix(".git"), m["branch"]
    if repo in (".", "..") or not repo:
        raise GitHubError("invalid repository name")
    if branch and (".." in branch or branch.endswith(("/", ".lock")) or "//" in branch):
        raise GitHubError("invalid branch name")
    return owner, repo, branch


def zip_url(owner: str, repo: str, branch: str | None) -> str:
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    return f"https://{ZIP_HOST}/{owner}/{repo}/zip/{ref}"


def fetch_zip(owner: str, repo: str, branch: str | None, transport=None, clock=time.monotonic) -> bytes:
    """The repository ZIP bytes, or GitHubError with a plain reason."""
    url = zip_url(owner, repo, branch)
    deadline = clock() + TOTAL_TIMEOUT_S
    timeout = httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
                      headers={"User-Agent": "MAYA-code-connector/0.1 (read-only)"}) as c:
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(url)
            if host.scheme != "https" or host.hostname != ZIP_HOST or host.port not in (None, 443) or host.username:
                raise GitHubError("GitHub redirected to another host; refused")
            try:
                with c.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code == 404:
                        raise GitHubError("repository or branch not found, or the repository is private "
                                          "(MAYA reads public repositories only)")
                    if r.status_code != 200:
                        raise GitHubError(f"GitHub answered HTTP {r.status_code}")
                    declared = r.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                        raise GitHubError("the repository ZIP is larger than 25 MB")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise GitHubError("the repository ZIP is larger than 25 MB")
                        if clock() > deadline:
                            raise GitHubError("GitHub took longer than 30 s")
                    return bytes(body)
            except httpx.TimeoutException:
                raise GitHubError("GitHub took too long to answer") from None
            except httpx.HTTPError as e:
                raise GitHubError(f"could not reach GitHub ({type(e).__name__})") from None
        raise GitHubError("too many redirects")
