"""Re-test after APPLIED: re-run exactly the checks that produced the findings, on the same paths."""
from src.models import Finding
from src.scanner.http_client import HttpClient
from src.scanner.runner import HTTP_CHECKS, run_check


def retest(client: HttpClient, base_url: str, before: dict[str, Finding]) -> dict:
    """status FIXED only if every originating finding is now absent; otherwise STILL_PRESENT.

    Returns a before/after diff: resolved, still_present, new (findings the same checks now report that
    were not there before), plus a per-check status.
    """
    targets = sorted({(f.check, f.location) for f in before.values() if f.check in HTTP_CHECKS})
    after: dict[str, Finding] = {}
    for check, path in targets:
        for f in run_check(client, base_url, check, path):
            after[f.id] = f
    resolved = sorted(set(before) - set(after))
    still = sorted(set(before) & set(after))
    new = sorted(set(after) - set(before))
    per_check = {}
    for f in before.values():
        per_check.setdefault(f.check, "FIXED")
        if f.id in after:
            per_check[f.check] = "STILL_PRESENT"
    return {"status": "FIXED" if not still else "STILL_PRESENT", "resolved": resolved, "still_present": still,
            "new": new, "per_check": dict(sorted(per_check.items())), "after": after}
