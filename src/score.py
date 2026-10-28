"""Heuristic score (A-F) of the checks that ran. Fixed formula, decided in code:

    start at 100; subtract per finding: critical 30, high 15, medium 6, low 2, info 0; floor at 0.
    grade: A >= 90, B >= 75, C >= 60, D >= 40, F below 40.

It is always labelled "heuristic score of the checks run": it says nothing about checks that did not run
or about issues no check looks for.
"""
from collections import Counter

WEIGHTS = {"critical": 30, "high": 15, "medium": 6, "low": 2, "info": 0}
GRADES = ((90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F"))
LABEL = "heuristic score of the checks run"


def grade(points: int) -> str:
    return next(g for floor, g in GRADES if points >= floor)


def score(severities) -> dict:
    counts = Counter(severities)
    unknown = set(counts) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"unknown severity: {sorted(unknown)}")
    points = max(0, 100 - sum(WEIGHTS[s] * n for s, n in counts.items()))
    return {"score": points, "grade": grade(points), "label": LABEL,
            "deductions": {s: counts[s] for s in WEIGHTS if counts[s]}}
