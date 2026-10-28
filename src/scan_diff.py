"""What changed between two scans of the same asset. Findings are matched by id (check + location)."""


def diff(before: list[dict], after: list[dict]) -> dict:
    """before/after: finding views ({id, severity, title, ...}). Severity changes are listed apart from
    "unchanged", so every id appears in exactly one list."""
    a, b = {f["id"]: f for f in before}, {f["id"]: f for f in after}
    changed = sorted(i for i in a.keys() & b.keys() if a[i]["severity"] != b[i]["severity"])
    out = {
        "new": sorted(b.keys() - a.keys()),
        "resolved": sorted(a.keys() - b.keys()),
        "unchanged": sorted(i for i in a.keys() & b.keys() if i not in changed),
        "severity_changed": [{"id": i, "from": a[i]["severity"], "to": b[i]["severity"]} for i in changed],
    }
    out["counts"] = {k: len(v) for k, v in out.items()}
    return out


def summary(d: dict) -> str:
    c = d["counts"]
    return (f"{c['new']} new, {c['resolved']} resolved, {c['unchanged']} unchanged, "
            f"{c['severity_changed']} severity change(s)")
