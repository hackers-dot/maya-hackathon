"""Outdated JavaScript libraries: read <script src> names on a page and compare their versions to a short table.

Pure function over a Response (no network, nothing is downloaded or executed). Wording is "appears outdated":
a version number alone does not show that a weakness is reachable on this site.
"""
import re

from src.models import Finding
from src.scanner.findings import from_response

# A SHORT EXAMPLE LIST, not a complete database: library -> first release this table treats as current
# enough. Anything older "appears outdated". Extend with care; the source of truth is each project's notes.
OLD_BEFORE = {
    "jquery": (3, 5, 0),
    "bootstrap": (4, 3, 1),
    "lodash": (4, 17, 21),
    "moment": (2, 29, 4),
    "angular": (1, 8, 3),  # AngularJS 1.x
}
SCRIPT_SRC = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']?([^"'\s>]+)""", re.I)
# name-1.2.3.js, name.1.2.3.min.js, name@1.2.3/..., name/1.2.3/name.min.js
NAME_VERSION = re.compile(r"(?:^|[/\-_.])(?P<name>[a-z][a-z.]*?)[-.@/]v?(?P<ver>\d+\.\d+\.\d+)(?=[./\-]|$)", re.I)


def script_libraries(html: str) -> list[tuple[str, tuple[int, int, int], str]]:
    """(library, version, src) for every <script src> whose path names a library in OLD_BEFORE."""
    found = []
    for src in SCRIPT_SRC.findall(html):
        path = src.split("?", 1)[0].split("#", 1)[0]
        for m in NAME_VERSION.finditer(path):
            name = m["name"].lower().removesuffix(".min").removesuffix(".slim")
            if name in OLD_BEFORE:
                found.append((name, tuple(int(x) for x in m["ver"].split(".")), src))
                break
    return found


def outdated(html: str) -> list[tuple[str, str, str]]:
    """(library, version string, src) for libraries older than the table's threshold."""
    return [(name, ".".join(map(str, ver)), src) for name, ver, src in script_libraries(html)
            if ver < OLD_BEFORE[name]]


def check_js_libraries(resp) -> list[Finding]:
    if resp.status != 200 or "html" not in (resp.header("content-type") or "").lower():
        return []
    html = resp.body.decode("utf-8", "replace")
    findings = []
    for name, version, src in outdated(html):
        current = ".".join(map(str, OLD_BEFORE[name]))
        findings.append(from_response(
            resp, "outdated_js_library", f'<script src="{src}">',
            f"The page loads {name} {version}, which appears outdated (MAYA's short example table treats "
            f"{name} releases before {current} as outdated).", suffix=f":{name}"))
    return findings
