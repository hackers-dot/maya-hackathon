# MAYA

MAYA is an AI security agent for sites and apps you own. You connect a target, prove you own it, and MAYA runs safe, non-destructive checks. It produces an evidence-backed report: which vulnerability, on which page, at what severity, with proof. It explains each finding in plain language and proposes a patch that a human must approve. After the patch is applied, MAYA re-runs the same check to prove the issue is fixed.

All code in this repository was written during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026). A private prototype existed before the event, but none of its code was reused here.

## Requirements

- **Python 3.10 or newer** (tested on 3.10.0, Windows).
- **Pinned dependencies** in `requirements.txt`: `httpx==0.28.1`, `pydantic==2.14.1`, `pytest==9.1.1`. Everything else is the Python standard library.
- **Optional tools:**
  - `openssl` on the PATH: used only by the 5 TLS tests (3 expiry cases, protocol, evidence), to make throwaway test certificates. Without it those tests are skipped, with the reason shown.
  - Google Chrome: used only by `scripts/ui_screens.py` (screenshots).
- **Model key (optional):** with no model key, scans run in Deterministic-only mode. With a key set (`BASETEN_API_KEY`), the agent uses the model. The key is read from the environment at call time and is never written to a file.

### Run it (Windows)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -v
.venv\Scripts\python scripts\run_web.py
```

Then open http://127.0.0.1:8090 (loopback only). Options: `--port 8095` and `--data-dir <folder>` (default `data/`, git-ignored).

Demo flow:

```
.venv\Scripts\python scripts\demo_fix_flow.py
.venv\Scripts\python scripts\demo_prepare.py --reset
```

`demo_fix_flow.py` runs scan, propose, approve, apply and re-test against the loopback demo app in the terminal, with no model. `demo_prepare.py --reset` prints the click order for the same flow in the browser: reset, scan, approve all, apply, re-test, download pack, chat question.

## Build log

- **2026-10-27 (Day 1):** Repo scaffold, design doc, `Finding` model, target scope guard (loopback or verified targets only), OpenAI-compatible model client that keeps reasoning separate from the final answer, and offline tests.
- **2026-10-27 (Day 2):** Deterministic core, no model needed: a fixed severity table, a SQLite evidence store with secret redaction and sha256 verification, a guarded HTTP client (the single egress point: scope check on every hop, GET/HEAD only, rate limit), header/cookie/version-disclosure checks, a loopback-only deliberately vulnerable demo shop with a fixed mode, and a token budget with a hard cap.
- **2026-10-27 (Day 4):** Full scanner (exposed `.env`, `.git`, backup files, directory listing, SPF/DMARC parsing), an allowlisted tool registry with strict argument validation, a bounded agent loop on native tool calling with the token budget wired in, and a verifier that keeps only typed claims backed by hash-verified, verbatim-quoted evidence. Report text comes from fixed templates.
- **2026-10-27 (Day 5):** Live agent run script against the loopback demo app (6 steps, 40k-token cap, low reasoning effort, `--dry-run` with a scripted model), a report builder (severity-sorted, "fix first" top 3, OWASP categories worded "mapped to", fixed plain-language and technical text, markdown + self-contained HTML), and ownership verification via a meta tag or `/.well-known/maya-verify.txt`. The scope guard now also refuses numeric host spellings even when they are on the verified list.
- **2026-10-27 (Day 5.5):** Fixes from the first live run. The model sent 15 `run_check` calls in one step and 11 were rejected, so the scan was incomplete; and every claim quoted homepage body text that did not support it. Now calls over the 4-per-step limit are deferred (answered, logged, retried later) instead of rejected; the prompt orders checks high-impact first; and the verifier checks entailment in code, so a claim is kept only if its quote is a line that actually shows the issue. The report shows that supporting line.
- **2026-10-27 (Day 5.6):** Fixes from the second live run. The scan finished (11 findings), but the model spent its last steps on `get_evidence` and never answered, so the report was empty. Now:
  - The report is built from all code-detected findings ("Detected by deterministic checks"), with model-verified claims as a separate section and a header note when the model did not finish.
  - The last step (or the step after every check type has been tried) forces a final answer with `tool_choice="none"`.
  - `run_check` returns an evidence summary line, and `get_evidence` is capped at 2 calls per run.
  - The three checks that cannot run over HTTP (SPF, DMARC, TLS) are no longer offered to the model.
- **2026-10-27 (Day 5.7):** Third live run: the model finished, but all 11 of its claims were dropped as "quote not found verbatim". The likely cause was our own instruction to quote the `evidence_summary` lines, which carry an id prefix that is not in the evidence. Now:
  - The model no longer writes quotes. Claims are `{finding_id, claim_type, priority, note}`, and code attaches the verbatim evidence line.
  - Notes are labelled "unverified model text", restricted words are masked, and a note that mentions a host, path or number not in the finding's evidence is dropped (the claim is kept).
  - Bad priorities fall back to severity order.
  - `--show-dropped` prints a quote-vs-evidence diagnostic for each dropped claim.
- **2026-10-27 (Day 6):** Fix proposals, approval gate and re-test.
  - Proposals come from fixed templates: nginx and Apache snippets, a Python note, a risk level and a rollback line, each referencing its finding ids and marked "proposed, not applied".
  - A human-named approval is required. Applying works only on the loopback demo app, through a control file, with a backup, a health check and automatic rollback. Every step is written to a hash-chained audit log.
  - The re-test reruns the same checks and reports FIXED only when every original finding is gone, with a before/after diff.
  - `scripts/demo_fix_flow.py` runs the whole flow without a model.
  - Small fixes: `.env` evidence now shows a masked secret-looking line, backups show a SQL/zip signature line, and notes may name the `Secure` cookie attribute.
- **2026-10-27 (Web step 1):** A stdlib web app on `127.0.0.1:8090` (`scripts/run_web.py`).
  - Security headers on every response, Host-header check, static files only from the repo, no inline scripts, and no `innerHTML` (enforced by a test).
  - Email + password accounts with scrypt hashing, a login limit of 5 failures per 5 minutes per account, HttpOnly + SameSite=Strict server-side sessions, and a CSRF token on every POST.
  - User flow: account type → what you have (only Website works for now) → connect yes/no → consent screen listing what MAYA will and will not do → placeholder chat.
- **2026-10-27 (Web 1.5):** UI redesign and automated UI checks.
  - A ChatGPT-style shell (sidebar, suggestion cards, disabled composer marked "Chat coming next", no fake replies) and centered cards for every onboarding step. Dark theme with a light toggle; responsive down to 360 px.
  - `scripts/ui_flow_check.py` drives the whole flow over HTTP with a throwaway data dir.
  - `scripts/ui_screens.py` screenshots every page at desktop and mobile size with the installed Chrome over the DevTools protocol, which gives an exact mobile viewport.
  - Fixes found by these checks: sign-out now returns a fresh CSRF token; tall cards no longer slide under the top controls on mobile; dead space under cards removed; the shell's logo mark is centered.
- **2026-10-27 (Web 2):** Connect a website, run scans, watch them live.
  - After consent, add a website: the URL is validated with the scope guard's rules, a verification token is issued (meta tag or `/.well-known` file), and "Verify ownership" checks it through the HTTP client. Any loopback URL means MAYA's own demo app, which needs no verification.
  - "Run security scan" works only for verified or demo assets, one scan per user at a time. It uses the agent with the real model when a key is configured, otherwise a clearly labelled deterministic-only mode, with the same hard caps.
  - A live trace dashboard polls every second (steps, tokens with reasoning separate, latency, status chips, findings table with severity badges). The result view shows the model's priority order with notes labelled "unverified model text", token usage, the model that ran, and a DEMO banner.
  - Another user's runs and assets return 404.
- **2026-10-27 (Web 3):** Report UI, fix flow and re-test in the browser.
  - The trace is grouped by model step. Each finding opens to explain itself for an Owner, Developer or Technical reader (fixed templates, OWASP "mapped to" label, code evidence), plus a "Fix first" top 3.
  - Fix proposals show nginx, Apache and Python snippets with copy buttons, risk, rollback and status. Approve needs a name; Apply works only on the loopback demo app and only for approved fixes (checked again by the existing approval gate); Re-test shows resolved / still present / new and FIXED.
  - An audit panel shows the verified hash chain and the latest entries, plus a token panel. Each user has their own demo app and audit log.
- **2026-10-28 (Web 4):** Chat, "approve all" and sorted fix proposals.
  - Chat for Individual and Company users, stored per user. It uses the existing model client (low reasoning effort, 1,200 max tokens) with a safety-first system prompt: simple English or Hindi/Hinglish, no exploit code, no help scanning sites you don't own.
  - For companies, the model gets only that user's own verified findings and must cite their ids. Replies are labelled "unverified model text" unless they only restate findings, and show the model and token counts.
  - Limits: 2,000 characters per message, 20 messages a minute, a daily chat token budget, and only the last 10 messages sent. Pasted secrets are masked. Without a model key the composer says "Model not configured" and never fakes a reply.
  - Fix proposals are sorted critical first, and "Approve all" still audits each approval separately.
- **2026-10-28 (Web 5):** Chat fix status, evidence pack, downloads and an eval harness.
  - The chat knows each finding's status (detected, applied, or fixed and re-tested) and is told never to call a fixed finding still present.
  - "Download evidence pack": a zip with the stored (redacted) evidence, a sha256 manifest and a hash-chained custody log that includes the run's approve / apply / re-test entries. It is re-verified before every download, and `verify_pack` names exactly which file or log line fails. Tamper-evident, not tamper-proof (unsigned); provenance is self-reported.
  - "Download report" in HTML or Markdown.
  - An offline eval: 3 demo-app states x 5 scripted model behaviours (perfect, sloppy, adversarial, truncated, no answer). Measured: 0 fake claims reached a report in 15 runs ([results](docs/eval_results.md)). Live model (`scripts/eval_live.py`, latest run 2026-10-28 UTC): 3 runs with `meta-models/muse-spark-open-1026` on the vulnerable demo app, 0 fake claims reached the report in each run, 12 of 12 findings detected and 12 claims kept per run, 14,589 / 14,687 / 14,621 tokens (cap 40,000 per scan). 3 scans on one demo app; not a general accuracy claim. A "Quality" panel shows these numbers from the generated file.
- **2026-10-28 (Web 5.1):** Demo polish.
  - A "Reset demo app" button returns your own demo app to its vulnerable state (audited as `demo_reset`; refused while a scan runs; other users' demo apps are untouched).
  - The chat sees your last 3 scans. After a clean re-scan it says "no issues detected in the checks run" and names the issues that were fixed and re-tested, instead of claiming nothing was scanned.
  - Evidence packs for the demo app are always marked DEMO, even when a scan found nothing, and an empty pack's README says "No issues detected in the checks run."
  - `scripts/demo_prepare.py --reset` prints the exact click order for a demo.
- **2026-10-28 (Web 6):** Chat accuracy, DNS and TLS checks, outdated JS libraries.
  - The chat sees, for each scan, "scan found N findings; later re-test: all fixed / M still open / no re-test", and is told never to say a scan found nothing when it found issues.
  - SPF and DMARC checks now run for verified (non-loopback) domains. **Privacy note:** MAYA looks up your domain's TXT records through a public DNS-over-HTTPS resolver (cloudflare-dns.com), so your domain name is sent to that provider. Nothing about the loopback demo app is sent.
  - A TLS check for verified https sites connects only to port 443 of that host and reports a certificate expiring within 14 days (high) or 30 days (medium), and protocols below TLS 1.2 (medium).
  - Outdated JavaScript libraries: `<script src>` names are compared to a short example table and reported as "appears outdated". The demo app now loads an old jQuery, so a scan of it finds 12 issues; the live eval above was measured before this change (11).
  - The result view lists which checks ran and which are "not applicable for this target".
- **2026-10-28 (Web 7):** Code connector, log upload and data deletion.
  - Source code ZIP upload (up to 10 MB): read in memory, never executed or saved, with path, symlink, size, file-count and zip-bomb guards. It flags hard-coded secrets (masked), a committed `.env`, `debug=True`, `eval`/`exec`, `pickle.loads`, `shell=True`, MD5/SHA-1 for passwords, and dependencies that appear outdated (a short example list).
  - Log upload (access logs and ssh auth logs, up to 5 MB): failed-login bursts, sensitive-path probing, many 4xx responses, injection-like strings (detection only) and a login after repeated failures. Findings are grouped by "shares source address", with configurable heuristic thresholds, and evidence lines are verbatim with credentials masked. An optional model summary uses typed claims verified like scans; without a key it says "Model not configured".
  - Upload findings go into the same findings table, report and evidence pack.
  - "Disconnect" per asset, and "Delete my data" (type DELETE): removes your assets, runs, evidence records, chats and demo app state, leaving one audit entry with counts only. A data-use note says what is stored, where (only on this computer) and for how long.
- **2026-10-28 (Web 8):** Suspicious message check.
  - Upload a screenshot of a message, ad or page that claims to come from your business. After you confirm that the image goes to the model provider, the model only reads the text: web addresses, phone numbers, brand names, payment requests and urgency phrases, as strict JSON.
  - MAYA's code then compares the addresses and numbers with your verified websites and an optional official list, and labels each one "resembles your domain" (fixed look-alike rules), "not in your list, needs review" or "matches your list". Each item is shown as "read by the model from the image (unverified)".
  - MAYA never visits the addresses. An optional DNS existence check goes only through the DNS-over-HTTPS resolver, for domains you confirm.
  - Findings go into the same table, report and evidence pack. The pack keeps the image hash and size, plus the image only if you choose.
  - Registrar and platform report notes come from fixed templates and become READY only after a named approval; MAYA never sends them.
- **2026-10-28 (Web 9):** Scan diff, heuristic score, developer export, DNS TXT ownership.
  - "What changed" compares two scans of the same site (new, resolved, unchanged, severity changed). "Re-scan" shows it automatically, and the chat sees one change line per scan pair.
  - A heuristic A-F score of the checks run (fixed formula: 100 minus 30/15/6/2 per critical/high/medium/low finding). It is shown before and after the re-test (demo: F to A), with a score history and the checks that did not run listed beside it.
  - "Copy as GitHub issue" per finding and "Download all as issues.md": fix snippets, rollback and the acceptance criterion "re-test shows FIXED", with secrets kept masked. A printable report can be saved as PDF from the browser.
  - A third ownership method: a DNS TXT record (`_maya-verify.<domain>` = `maya-verify=<token>`), looked up through the DNS-over-HTTPS resolver, so the domain name is sent to cloudflare-dns.com.
  - Offline eval not regenerated: finding and claim counts are unchanged (checked row by row).

## License

MIT. See [LICENSE](LICENSE).
