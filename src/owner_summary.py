"""Summary for the owner: plain language, built ONLY from stored data with fixed templates.

Score and grade (always labelled heuristic), counts by severity, the top 3 "do this first" items ordered by the
fixed severity table with each fix's change risk from the fix templates, what is already fixed and re-tested,
and which checks could not run. No time estimates, no costs, and never "you are safe".
The optional model reword (see check_reword) may only rephrase: a reply with a number that is not in the
summary, or with a reassurance word, is dropped.
"""
import re

from src.fixes import PLANS

SEVERITIES = ("critical", "high", "medium", "low", "info")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# reassurance, and time or cost estimates: the summary never makes either, so a reword may not add them
FORBIDDEN = re.compile(r"(?i)\b(?:safe|secure|protected|hacked|guarantee[ds]?|compliant|hours?|minutes?|days?|weeks?|"
                       r"months?|costs?|price[sd]?|dollars?|rupees?|euros?)\b|[$₹€]")
LANGS = ("en", "hinglish")


def build(findings: list[dict], score: dict | None, checks: list[dict], resolved: list[str] | None,
          after: dict | None = None) -> dict:
    """findings: finding views of the run; score: run_score(run) or None; resolved: ids a re-test showed gone;
    after: retest_score(run) or None (the heuristic score of what the re-test still saw)."""
    resolved = set(resolved or [])
    open_items = [f for f in findings if f["id"] not in resolved]
    counts = {s: sum(1 for f in open_items if f["severity"] == s) for s in SEVERITIES}
    top = sorted(open_items, key=lambda f: (SEVERITIES.index(f["severity"]), f["check"], f["location"]))[:3]
    top = [{"id": f["id"], "title": f["title"], "severity": f["severity"],
            "risk": PLANS[f["check"]].risk if f["check"] in PLANS else "no fix template"} for f in top]
    fixed = [f for f in findings if f["id"] in resolved]
    not_run = [c for c in checks if c.get("status") != "ran"]
    total = sum(counts.values())
    sev_text = ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts[s])
    reasons = sorted({c["status"] for c in not_run})
    not_run_names = ", ".join(c["check"] for c in not_run)

    en, hi = [], []
    # HINGLISH strings below NEED NATIVE-SPEAKER REVIEW before they are relied on.
    if score:
        en.append(f"Heuristic score of the checks run, at the time of this scan: {score['grade']} "
                  f"({score['score']}/100). It covers only the checks MAYA ran.")
        hi.append(f"Heuristic score (sirf jo checks chale unka), is scan ke waqt: {score['grade']} "
                  f"({score['score']}/100). Yeh sirf MAYA ke chalaye checks par based hai.")
        if after:
            en.append(f"After the re-test: {after['grade']} ({after['score']}/100).")
            hi.append(f"Re-test ke baad: {after['grade']} ({after['score']}/100).")
    else:
        en.append("No heuristic score for this result: scores are given only for website and demo-app scans.")
        hi.append("Is result ka koi heuristic score nahi hai: score sirf website aur demo-app scans ke liye hai.")
    if total:
        en.append(f"Open issues found by MAYA's checks: {total} ({sev_text}).")
        hi.append(f"MAYA ke checks ne {total} open issue(s) dhoondhe ({sev_text}).")
        en.append("Do this first:")
        hi.append("Pehle yeh karein:")
        for n, t in enumerate(top, 1):
            en.append(f"{n}. {t['title']} (severity {t['severity']}; risk of the change: {t['risk']}).")
            hi.append(f"{n}. {t['title']} (severity {t['severity']}; change ka risk: {t['risk']}).")
    else:
        en.append("No open issues were detected in the checks run.")
        hi.append("Jo checks chale, unmein koi open issue nahi mila.")
    if fixed:
        en.append(f"Already fixed and re-tested: {len(fixed)} issue(s).")
        hi.append(f"Fix karke re-test ho chuke: {len(fixed)} issue(s).")
    else:
        en.append("Nothing has been fixed and re-tested yet.")
        hi.append("Abhi tak kuch bhi fix karke re-test nahi hua.")
    if not_run:
        en.append(f"Checks that could not run here: {not_run_names}. Reason: {'; '.join(reasons)}.")
        hi.append(f"Yeh checks yahan nahi chal sake: {not_run_names}. Wajah: {'; '.join(reasons)}.")
    lines = {"en": en, "hinglish": hi}
    numbers = sorted({n for ls in lines.values() for line in ls for n in NUMBER.findall(line)}, key=float)
    return {"score": score, "counts": counts, "top": top, "fixed": [f["id"] for f in fixed],
            "not_run": not_run, "lines": lines, "numbers": numbers}


def check_reword(reply: str, summary: dict, lang: str) -> tuple[str | None, str | None]:
    """(text, None) if the model only reworded; (None, reason) if it added a number or a reassurance."""
    text = (reply or "").strip()
    if not text:
        return None, "the model returned no text"
    allowed = set(NUMBER.findall(" ".join(summary["lines"][lang])))
    extra = sorted(set(NUMBER.findall(text)) - allowed)
    if extra:
        return None, f"the reply contained numbers that are not in the summary: {extra[:5]}"
    if FORBIDDEN.search(text):
        return None, f"the reply used a word MAYA never uses for results: {FORBIDDEN.search(text).group(0)!r}"
    return text[:2000], None
