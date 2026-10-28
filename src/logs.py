"""Small SOC module: parse uploaded access / auth logs and flag suspicious patterns. Pure functions, no network.

Supported lines: nginx/Apache "combined"/"common" access logs, and syslog-style sshd auth lines.
Detections are HEURISTICS with configurable thresholds, not a standard; they describe activity that
"shares source address", never who is behind it. Evidence lines are kept verbatim, except that credentials
inside a line are masked (src.evidence.redact_text); IP addresses stay visible.
"""
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote_plus

from src.evidence import redact_text
from src.models import Finding
from src.scanner.findings import make_finding

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_LINES = 100_000
MAX_LINE_CHARS = 2000
EVIDENCE_LINES = 8           # lines kept per finding (the first ones, verbatim); the count says how many in total

# configurable heuristics (defaults documented in docs/design.md)
FAILED_LOGIN_THRESHOLD = 10  # failed logins from one source address ...
FAILED_LOGIN_WINDOW_S = 300  # ... within 5 minutes
MANY_4XX_THRESHOLD = 20      # 4xx responses to one source address in the whole file
FAILS_BEFORE_SUCCESS = 3     # failed logins before a successful one from the same source address

LOG_CHECKS = ("log_failed_login_burst", "log_sensitive_path_probe", "log_many_4xx", "log_injection_pattern",
              "log_login_after_failures")

ACCESS = re.compile(r'^(?P<ip>[0-9A-Fa-f:.]+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<req>[^"]*)" (?P<status>\d{3}) ')
AUTH = re.compile(r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}) (?P<hms>\d\d:\d\d:\d\d) \S+ sshd\[\d+\]: (?P<msg>.*)$")
SSH_FAIL = re.compile(r"(?:Failed password for (?:invalid user )?\S+|Invalid user \S+) from (?P<ip>[0-9A-Fa-f:.]+)")
SSH_OK = re.compile(r"Accepted (?:password|publickey|keyboard-interactive/pam) for \S+ from (?P<ip>[0-9A-Fa-f:.]+)")
SENSITIVE_PATH = re.compile(r"(?i)/\.env\b|/\.git(?:/|\b)|wp-login\.php|/phpmyadmin")
LOGIN_PATH = re.compile(r"(?i)login|signin|auth")
INJECTION = re.compile(r"(?i)\bunion\b[\s+]+\bselect\b|'\s*or\s*'?\d+'?\s*=\s*'?\d+|\bor\s+1\s*=\s*1\b|"
                       r"\bsleep\s*\(\s*\d+\s*\)|;\s*drop\s+table\b|\.\./|\.\.\\|/etc/passwd|\bwin\.ini\b")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                      "Nov", "Dec"], 1)}
SYSLOG_YEAR = 2000  # syslog lines carry no year; only time differences inside one file are used


def parse(text: str) -> list[dict]:
    """One event per recognised line: {n, line, ip, t (epoch s or None), kind, status, path, request}."""
    events = []
    for n, raw in enumerate(text.splitlines()[:MAX_LINES], 1):
        line = raw[:MAX_LINE_CHARS]
        m = ACCESS.match(line)
        if m:
            parts = m["req"].split(" ")
            try:
                t = datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z").timestamp()
            except ValueError:
                t = None
            path = parts[1] if len(parts) > 1 else ""
            status = int(m["status"])
            kind = "access"
            if parts[0] == "POST" and LOGIN_PATH.search(path) and status in (401, 403):
                kind = "login_fail"
            events.append({"n": n, "line": line, "ip": m["ip"], "t": t, "kind": kind, "status": status,
                           "path": path, "request": m["req"]})
            continue
        m = AUTH.match(line)
        if m:
            t = datetime(SYSLOG_YEAR, MONTHS.get(m["mon"], 1), int(m["day"]), *map(int, m["hms"].split(":")),
                         tzinfo=timezone.utc).timestamp()
            fail, ok = SSH_FAIL.search(m["msg"]), SSH_OK.search(m["msg"])
            if fail or ok:
                events.append({"n": n, "line": line, "ip": (fail or ok)["ip"], "t": t,
                               "kind": "login_fail" if fail else "login_ok", "status": None, "path": "",
                               "request": ""})
    return events


def _burst(times: list[float], threshold: int, window: float) -> bool:
    j = 0
    for i, t in enumerate(times):
        while t - times[j] > window:
            j += 1
        if i - j + 1 >= threshold:
            return True
    return False


def detect(events: list[dict], failed_threshold: int = FAILED_LOGIN_THRESHOLD,
           window_s: int = FAILED_LOGIN_WINDOW_S, many_4xx: int = MANY_4XX_THRESHOLD,
           fails_before_success: int = FAILS_BEFORE_SUCCESS) -> dict[tuple[str, str], list[dict]]:
    """{(check, source address): [events]} for every detection."""
    by_ip = defaultdict(list)
    for e in events:
        by_ip[e["ip"]].append(e)
    hits = {}
    for ip, evs in by_ip.items():
        fails = [e for e in evs if e["kind"] == "login_fail"]
        times = sorted(e["t"] for e in fails if e["t"] is not None)
        if len(times) >= failed_threshold and _burst(times, failed_threshold, window_s):
            hits[("log_failed_login_burst", ip)] = fails
        probes = [e for e in evs if e["path"] and SENSITIVE_PATH.search(unquote_plus(e["path"]))]
        if probes:
            hits[("log_sensitive_path_probe", ip)] = probes
        errors = [e for e in evs if e["status"] and 400 <= e["status"] < 500]
        if len(errors) >= many_4xx:
            hits[("log_many_4xx", ip)] = errors
        injected = [e for e in evs if e["request"] and INJECTION.search(unquote_plus(e["request"]))]
        if injected:
            hits[("log_injection_pattern", ip)] = injected
        prior, after = [], []
        for e in sorted(evs, key=lambda e: (e["t"] or 0, e["n"])):
            if e["kind"] == "login_fail":
                prior.append(e)
            elif e["kind"] == "login_ok" and len(prior) >= fails_before_success:
                after = prior + [e]  # every failure before the success, then the success itself
                break
        if after:
            hits[("log_login_after_failures", ip)] = after
    return hits


DESCRIBE = {
    "log_failed_login_burst": "{n} failed logins share source address {ip} ({th}+ within {w} minutes)",
    "log_sensitive_path_probe": "{n} request(s) for sensitive paths (.env, .git, wp-login, phpmyadmin) share "
                                "source address {ip}",
    "log_many_4xx": "{n} client-error (4xx) responses share source address {ip}",
    "log_injection_pattern": "{n} request line(s) with SQL-injection-like or path-traversal strings share source "
                             "address {ip} (detected in the log only; this does not show they succeeded)",
    "log_login_after_failures": "a successful login followed {k} failed login(s); all share source address {ip}",
}


def evidence_text(evs: list[dict]) -> str:
    """Verbatim lines with credentials masked (IP addresses stay visible)."""
    return "\n".join(f"L{e['n']}: {redact_text(e['line'])}" for e in evs[:EVIDENCE_LINES])


def log_findings(text: str, upload: str, store=None, **thresholds) -> tuple[list[Finding], dict, dict]:
    """(findings, groups {source address: [finding ids]}, counts)."""
    events = parse(text)
    findings, groups = [], defaultdict(list)
    for (check, ip), evs in sorted(detect(events, **thresholds).items()):
        # the successful login is the point of this finding: keep it (and the failures just before it)
        body = evidence_text(evs[-EVIDENCE_LINES:] if check == "log_login_after_failures" else evs)
        location = f"ip:{ip}"
        eid = store.add("LOG", f"log://{upload}#L{evs[0]['n']}", [], 0, [], body.encode()) if store else None
        technical = DESCRIBE[check].format(n=len(evs), ip=ip, th=thresholds.get("failed_threshold",
                                           FAILED_LOGIN_THRESHOLD), w=thresholds.get("window_s",
                                           FAILED_LOGIN_WINDOW_S) // 60, k=len(evs) - 1) + "."
        first = body.splitlines()[0] + (f" (+{len(evs) - 1} more line(s))" if len(evs) > 1 else "")
        f = make_finding(check, f"log:{upload}", location, f"{upload} lines {evs[0]['n']}..{evs[-1]['n']}", first,
                         technical, eid)
        findings.append(f)
        groups[ip].append(f.id)
    counts = {"lines": min(len(text.splitlines()), MAX_LINES), "parsed": len(events),
              "truncated": len(text.splitlines()) > MAX_LINES}
    return findings, dict(groups), counts


def support_lines(check: str, body: str) -> list[str]:
    """Stored evidence lines that still show the detection (for verifying model claims about log findings)."""
    preds = {
        "log_failed_login_burst": lambda l: bool(SSH_FAIL.search(l)) or (" 401 " in l or " 403 " in l),
        "log_sensitive_path_probe": lambda l: bool(SENSITIVE_PATH.search(unquote_plus(l))),
        "log_many_4xx": lambda l: bool(re.search(r'" 4\d\d ', l)),
        "log_injection_pattern": lambda l: bool(INJECTION.search(unquote_plus(l))),
        "log_login_after_failures": lambda l: bool(SSH_OK.search(l) or SSH_FAIL.search(l)),
    }
    pred = preds.get(check)
    return [l for l in body.splitlines() if pred and pred(l)]
