# MAYA

MAYA is an AI security agent for sites and apps you own. You connect a target, prove you own it, and MAYA runs safe, non-destructive checks. It produces an evidence-backed report: which vulnerability, on which page, at what severity, with proof. It explains each finding in plain language and proposes a patch that a human must approve. After the patch is applied, MAYA re-runs the same check to prove the issue is fixed.

All code in this repository was written during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026). A private prototype existed before the event, but none of its code was reused here.

## Build log

- **2026-10-27 (Day 1):** Repo scaffold, design doc, `Finding` model, target scope guard (loopback or verified targets only), OpenAI-compatible model client that keeps reasoning separate from the final answer, and offline tests.
- **2026-10-27 (Day 2):** Deterministic core, no model needed: a fixed severity table, a SQLite evidence store with secret redaction and sha256 verification, a guarded HTTP client (the single egress point: scope check on every hop, GET/HEAD only, rate limit), header/cookie/version-disclosure checks, a loopback-only deliberately vulnerable demo shop with a fixed mode, and a token budget with a hard cap.
- **2026-10-28 (Day 4):** Full scanner (exposed `.env`, `.git`, backup files, directory listing, SPF/DMARC parsing), an allowlisted tool registry with strict argument validation, a bounded agent loop on native tool calling with the token budget wired in, and a verifier that keeps only typed claims backed by hash-verified, verbatim-quoted evidence. Report text comes from fixed templates.
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

## License

MIT. See [LICENSE](LICENSE).
