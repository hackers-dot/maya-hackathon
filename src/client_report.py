"""Client report: one self-contained HTML file to download and share by hand (MAYA creates no public links).

Every string is escaped. Evidence lines are the stored, masked lines. Fixed wording only: findings are
"detected"; the score is a "heuristic score of the checks run"; the evidence-pack disclaimer is quoted as is.
Styling is inline because the file must work when opened from disk; the Print button uses the same external
/static/print.js as the printable report (it works when the file is opened from MAYA; from disk, use
Ctrl+P / Cmd+P as the page says).
"""
import html
import time

from src.evidence_pack import DISCLAIMER
from src.fixes import PLANS
from src.report import DEMO_BANNER, SCOPE_NOTE, TEXT, owasp_label

CSS = ("body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.5;"
       "color:#1d1d1f;background:#fff}h1{margin-bottom:.2rem}code,pre{background:#f2f2f4;border-radius:4px}"
       "code{padding:0 .25rem;overflow-wrap:anywhere}pre{padding:.6rem;overflow-x:auto;white-space:pre-wrap}"
       ".banner{background:#fff3cd;border:1px solid #e0b100;padding:.6rem;border-radius:6px;font-weight:600}"
       ".sev{display:inline-block;padding:0 .45rem;border-radius:4px;color:#fff;font-size:.85rem}"
       ".critical{background:#8b0000}.high{background:#c0392b}.medium{background:#d35400}.low{background:#2c7a7b}"
       ".info{background:#555}section{border-top:1px solid #ddd;margin-top:1rem;padding-top:.4rem}"
       ".muted{color:#555}.print-bar{background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:.5rem .8rem}"
       ".print-bar button{font:inherit;font-weight:600;border:1px solid #4f46e5;background:#4f46e5;color:#fff;"
       "border-radius:6px;padding:.3rem .8rem;margin-right:.5rem;cursor:pointer}"
       "@media print{.print-bar{display:none}body{max-width:none;margin:0}section{break-inside:avoid}"
       ".sev,.banner{-webkit-print-color-adjust:exact;print-color-adjust:exact}}")


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _finding(f: dict) -> str:
    t, plan = TEXT[f["check"]], PLANS.get(f["check"])
    parts = [f'<section><h3><span class="sev {_e(f["severity"])}">{_e(f["severity"].upper())}</span> {_e(t.title)}</h3>',
             f"<p>Location: <code>{_e(f['location'])}</code> · Check <code>{_e(f['check'])}</code> · "
             f"{_e(owasp_label(f['check']))}</p>",
             f"<p>Evidence (detected by MAYA's checks; secrets masked): <code>{_e(f.get('evidence_line') or '-')}</code></p>",
             f"<p><strong>What this means:</strong> {_e(t.plain)}</p>",
             f"<p><strong>Suggested fix:</strong> {_e(t.fix)}</p>"]
    if f.get("context"):
        parts.append(f'<p class="muted">Found in {_e(f["context"])} (listed one severity level lower).</p>')
    if plan:
        parts += [f"<p>nginx:</p><pre>{_e(plan.nginx)}</pre>", f"<p>Apache:</p><pre>{_e(plan.apache)}</pre>",
                  f"<p>Risk of the change: {_e(plan.risk)}. Rollback: {_e(plan.rollback)}</p>"]
    return "".join(parts) + "</section>"


def build(label: str, run_id: str, findings: list[dict], score: dict | None, demo: bool, diff: dict | None,
          retest: dict | None, manifest_sha256: str | None, created: float) -> str:
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(created))
    b = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>MAYA client report: {_e(label)}</title><style>{CSS}</style>",
         '<script src="/static/print.js" defer></script></head><body>',
         '<p class="print-bar"><button id="print-btn" type="button">Print or save as PDF</button>'
         "If the button does nothing (file opened from disk), press Ctrl+P or Cmd+P.</p>",
         f"<h1>MAYA client report</h1><p class=\"muted\">{_e(label)} · run <code>{_e(run_id)}</code> · {_e(when)}</p>"]
    if demo:
        b.append(f'<p class="banner">{_e(DEMO_BANNER)}</p>')
    if score:
        b.append(f"<p><strong>Heuristic score of the checks run:</strong> {_e(score['grade'])} "
                 f"({_e(score['score'])}/100). It covers only the checks MAYA ran.</p>")
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("critical", "high", "medium", "low", "info")}
    b.append(f"<p>Issues detected: {len(findings)} (" +
             _e(", ".join(f"{n} {s}" for s, n in counts.items() if n) or "none") + ").</p>")
    main = [f for f in findings if not f.get("context")]
    test = [f for f in findings if f.get("context")]
    b.append("<h2>Findings</h2>" + ("".join(_finding(f) for f in main) or "<p>No issues detected in the checks run.</p>"))
    if test:
        b.append("<h2>Detected in test or demo code</h2>" + "".join(_finding(f) for f in test))
    b.append("<h2>What changed since the previous scan</h2>")
    if diff:
        c = diff["counts"]
        b.append(f"<p>{_e(c['new'])} new, {_e(c['resolved'])} resolved, {_e(c['unchanged'])} unchanged, "
                 f"{_e(c['severity_changed'])} severity change(s).</p>")
        for key, title in (("new", "New"), ("resolved", "Resolved")):
            if diff[key]:
                b.append(f"<p>{title}:</p><ul>" + "".join(f"<li><code>{_e(i)}</code></li>" for i in diff[key]) + "</ul>")
    else:
        b.append("<p class=\"muted\">No earlier completed scan of this asset to compare with.</p>")
    b.append("<h2>Re-test</h2>")
    if retest:
        b.append(f"<p>Re-test result: <strong>{_e(retest['status'])}</strong>. Resolved: {len(retest['resolved'])}; "
                 f"still present: {len(retest['still_present'])}; new: {len(retest['new'])}.</p>")
    else:
        b.append('<p class="muted">No re-test has been run for this scan.</p>')
    b.append("<h2>Evidence pack</h2>")
    b.append(f"<p>Manifest SHA-256 of the evidence pack for this run: <code>{_e(manifest_sha256 or 'not available')}</code>. "
             "Download the pack from MAYA to check it.</p>")
    b.append(f"<p>{_e(DISCLAIMER)}</p><p class=\"muted\">{_e(SCOPE_NOTE)}</p></body></html>")
    return "".join(b)
