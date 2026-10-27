# MAYA

MAYA is an AI security agent for sites and apps you own. You connect a target, prove you own it, and MAYA runs safe, non-destructive checks. It produces an evidence-backed report: which vulnerability, on which page, at what severity, with proof. It explains each finding in plain language and proposes a patch that a human must approve. After the patch is applied, MAYA re-runs the same check to prove the issue is fixed.

All code in this repository was written during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026). A private prototype existed before the event, but none of its code was reused here.

## Build log

- **2026-10-27 (Day 1):** Repo scaffold, design doc, `Finding` model, target scope guard (loopback or verified targets only), OpenAI-compatible model client that keeps reasoning separate from the final answer, and offline tests.
- **2026-10-27 (Day 2):** Deterministic core, no model needed: a fixed severity table, a SQLite evidence store with secret redaction and sha256 verification, a guarded HTTP client (the single egress point: scope check on every hop, GET/HEAD only, rate limit), header/cookie/version-disclosure checks, a loopback-only deliberately vulnerable demo shop with a fixed mode, and a token budget with a hard cap.
- **2026-10-28 (Day 4):** Full scanner (exposed `.env`, `.git`, backup files, directory listing, SPF/DMARC parsing), an allowlisted tool registry with strict argument validation, a bounded agent loop on native tool calling with the token budget wired in, and a verifier that keeps only typed claims backed by hash-verified, verbatim-quoted evidence. Report text comes from fixed templates.
- **2026-10-29 (Day 5):** Live agent run script against the loopback demo app (6 steps, 40k-token cap, low reasoning effort, `--dry-run` with a scripted model), a report builder (severity-sorted, "fix first" top 3, OWASP categories worded "mapped to", fixed plain-language and technical text, markdown + self-contained HTML), and ownership verification via a meta tag or `/.well-known/maya-verify.txt`. The scope guard now also refuses numeric host spellings even when they are on the verified list.
- **2026-10-29 (Day 5.5):** Fixes from the first live run. The model sent 15 `run_check` calls in one step and 11 were rejected, so the scan was incomplete; and every claim quoted homepage body text that did not support it. Now calls over the 4-per-step limit are deferred (answered, logged, retried later) instead of rejected; the prompt orders checks high-impact first; and the verifier checks entailment in code, so a claim is kept only if its quote is a line that actually shows the issue. The report shows that supporting line.

## License

MIT. See [LICENSE](LICENSE).
