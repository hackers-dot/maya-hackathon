# MAYA design

## Agent loop

```
connect target -> scope guard -> plan checks -> run checks (tools) -> store evidence
   -> verifier -> Finding -> explain -> propose patch -> APPROVAL GATE (human)
   -> apply patch -> re-run same check -> verified fixed / still vulnerable
```

The model plans, explains and drafts patches. It never touches the network directly. Every network action goes through a tool, and every tool goes through the scope guard.

## Ownership verification and scope

- `src/scope.py` is the single gate. A target is allowed only if:
  - its host is loopback (`localhost`, `127.0.0.0/8`, `::1`), which is our own demo app, or
  - its exact hostname is a dotted DNS name on the verified-targets list (empty by default).
- The guard refuses ambiguous URLs outright: userinfo (`http://127.0.0.1@evil.com`), backslashes, non-http(s) schemes, non-loopback IP literals, and non-canonical numeric host forms (decimal `2130706433`, hex `0x7f000001`, short `127.1`). Numeric forms are refused **even if they are on the verified list**, and so are single-label names like `intranet`.
- **Ownership verification (`src/ownership.py`):**
  - `new_token()` returns `maya-verify-` plus a random 192-bit URL-safe value.
  - The owner proves control in one of two ways: a homepage `<meta name="maya-verify" content="TOKEN">` (found with a real HTML parser, so comments don't count), or `/.well-known/maya-verify.txt` whose whole content is the token. Comparison is constant-time.
  - The check uses a one-off `HttpClient` whose allow-list holds only the user-submitted host. It fetches exactly those two paths with GET. A redirect to any other host is refused by the scope guard, and the submitted URL must pass the same URL rules as any scan.
  - On success the host is written to `data/verified_targets.json` with a UTC timestamp and method. `VerifiedTargets.hosts()` feeds `HttpClient(verified=...)`.
  - Loopback returns "verified (loopback)" without fetching or persisting anything.
  - Not covered yet: DNS-rebinding defense (a verified name resolving to an internal IP). That needs resolve-then-pin in the HTTP client.

## Reports: `src/report.py`

- **The report does not depend on the model finishing.** Its main section, "Detected by deterministic checks", is built from ALL findings code produced in the run. Each item shows its severity, location, evidence id and the code-derived supporting line from the hash-verified record (same rules as the verifier).
- Typed model claims that pass the verifier appear in a separate "Model-verified claims" section, in the model's priority order (or severity order, with a note saying why). Each one shows the verified code evidence line next to the model's plain-language note, which is labelled "unverified model text". Dropped claims are listed with reasons.
- The header shows whether the model finished. If it did not: "model did not finish; findings are from code checks".
- A run with no findings says "No issues detected in the checks run." (never "secure").
- Items are sorted by severity from the fixed table; "Fix first" is the top 3.
- Each item has its location, evidence id, verified quote, and an OWASP Top 10 2021 category taken from a fixed table and worded "mapped to" (SPF/DMARC are labelled as outside the OWASP Top 10). It also has a plain explanation with an analogy, a technical detail line and a fix, all from fixed per-check templates in code.
- The header shows the model, reasoning effort and token usage (reasoning tokens separate), plus a DEMO banner when any item comes from the demo app.
- Wording uses "appears" / "detected" / "not detected in the checks run". Tests strip code spans and assert that "secure", "hack" and "compliant" never appear in the prose. The only place the cookie attribute `Secure` appears is inside code formatting.
- The report is written as markdown plus a self-contained HTML file: inline CSS, no scripts, no external assets, and every dynamic string `html.escape`d.

## Live run: `scripts/live_agent_run.py`

Starts the demo app on a free loopback port (`--fixed` for fixed mode) and runs the agent with the real provider. Hard caps: 6 steps, 40,000 tokens (checked after each call, so the last call can overshoot by up to one reply), and `reasoning_effort=low`. Per step it prints the tool, args, result summary, tokens (reasoning separate) and model latency, then the verified and dropped claims, and writes the report to `data/reports/`. `--dry-run` uses a scripted `MockProvider`, so the only network traffic is to the loopback demo app.

## Single egress point: `src/scanner/http_client.py`

`HttpClient` is the ONLY code that makes outbound HTTP for scanning. It enforces:
- `scope.check(url)` before every request, **including every redirect hop**. Redirects are followed manually (max 5), so a redirect to an out-of-scope host is refused before it is requested.
- Only GET and HEAD. Any other method raises `MethodNotAllowed` before anything is sent.
- 5 s timeout, at most 1 request per 0.2 s, a fixed User-Agent `MAYA-scanner/0.1 (authorized security check)`.
- `trust_env=False`, so no proxy from the environment can reroute scan traffic.
- At most 256 KB of a response body is read.
- Every response is written to the evidence store, and the response carries its `evidence_id`.

## Scanner checks

Checks are read-only, non-destructive observations. No exploit code, no attack payloads, no brute force. Every check is a pure function (no network); fetching happens only in `HttpClient`.
- `checks_headers.py`: missing CSP/HSTS/X-Frame-Options/X-Content-Type-Options, cookie flags (Secure/HttpOnly/SameSite), and version disclosure in `Server`/`X-Powered-By`.
- `checks_files.py`: exposed `/.env` (non-HTML 200 with `KEY=value` lines), `/.git/HEAD` (`ref: refs/...` or a 40-hex SHA), common backup files (`/backup.zip`, `/db.sql`, `/index.php.bak`; non-HTML 200), and directory listings (`Index of /`, `Directory listing for /`). HTML 200s are ignored to avoid catch-all false positives. Body excerpts are redacted before they go into a Finding.
- `checks_dns.py`: SPF (missing, more than one record, `+all`/bare `all`) and DMARC (missing, duplicates, no valid `p=`), parsed from TXT strings. The lookup itself is not implemented (stdlib has no TXT resolver; adding one needs a dependency).
- `findings.py` builds every `Finding`: severity from the table, `id = check:location[:suffix]`, and `demo=True` when the target is loopback (our demo app).
- `runner.py`: `run_check(client, base, check_id, path)` for the agent, and `scan_all()`, a deterministic full scan. DNS/TLS checks raise `NotRunnable` over HTTP.

## Agent: `src/agent/`

- **`tools.py`: allowlisted tools.** `run_check(check_id, path)`, `list_findings()`, `get_evidence(id)`.
  - Every call is validated before anything runs: the tool name must be on the allowlist, args must be a JSON-object string with exactly the expected keys, `check_id` must be from the table, and `path` must start with a single `/` with no scheme, host, `..`, `@`, backslash or whitespace. Evidence ids must be 32-char hex.
  - Rejected calls are logged and never executed. Accepted calls are executed by code and logged (args redacted).
- **`loop.py`: bounded loop on native tool calling** (`tools` + `tool_choice="auto"`).
  - Stops after `MAX_STEPS` (default 8), and executes at most 4 tool calls per step.
  - **The final answer is forced.** The last step is reserved: on it, or as soon as every runnable check type has been attempted, the loop sends `tool_choice="none"` plus an explicit "write the final claims now" instruction. Tool calls returned on that step are ignored, never executed. `stop_reason` is `final` (the model chose to finish), `forced_final` or `budget_exceeded`; `final_answer_valid` says whether parseable claims came back.
  - **Only runnable checks are offered.** The `run_check` enum and the system prompt list exactly the 12 HTTP-runnable checks. `missing_spf`, `missing_dmarc` and `weak_tls_or_expiring_cert` stay in the severity table but are hidden from the model, and rejected if asked for, until they can run.
  - **`run_check` returns an `evidence_summary` line** per finding (`finding id | check | supporting line`), so the model can quote without `get_evidence`. `get_evidence` is capped at 2 calls per run; further calls return "limit reached" and are logged as `limited`.
  - Extra calls in a step are **deferred**, not rejected: they are never executed, they are logged with status `deferred`, and each one still gets a tool result (`"deferred: per-step limit reached, call again next step"`), so every `tool_call_id` is answered and the API conversation stays valid. The model can repeat them in a later step.
  - The system prompt tells the model: at most 4 `run_check` calls per step; high-impact checks first (`.env`, `.git`, backup files, directory listing), then cookies, then headers; continue until every check type has been tried; quote the line that shows the issue. `reasoning_effort` and `max_tokens` come from `config.yaml` (low, 1500) through the provider.
  - Every reply's usage, including `reasoning_tokens`, goes into `TokenBudget`. `BudgetExceeded` stops the loop cleanly before any further tool runs.
  - Every step writes a trace entry: step, tool, args (redacted), result summary, tokens.
  - The final answer must be JSON claims; there is no free-text report.
- **`verifier.py`: claim verification.**
  - **Day 5.7 change: the model no longer writes quotes.** In the third live run all 11 claims were dropped as "quote not found verbatim". The Day 5.6 final instruction told the model to quote its `evidence_summary` lines, and those start with a `finding id | check |` prefix that is not in the record (`--show-dropped` reproduces this mismatch in the dry run).
  - Claims are now `{finding_id, claim_type (enum), priority (1..N), note (≤160 chars)}`. Extra keys are forbidden (a model-supplied `severity` makes the claim malformed). A legacy `quoted_evidence` is accepted but ignored, kept only for diagnostics.
  - Code attaches the evidence line itself: the first support line from the hash-verified record.
  - A claim is kept if: (1) its finding exists, (2) `claim_type` matches the finding's check, (3) the evidence hash verifies, and (4) the record satisfies the pattern table below.
  - **The model's note is unverified text.** Restricted words (fake, scam, hack…, secure, compliant) are masked. Every host, path or number in the note must appear in that finding's own evidence, and anything over 160 chars is too long; a note that fails is dropped, and the claim is kept.
  - **Order:** the model's priorities are used only if they are present and unique among verified claims. Otherwise claims fall back to severity order, and the report says so.
  - `scripts/live_agent_run.py --show-dropped` prints, for each dropped claim, the model's quote, the evidence line it should have matched, and a diff hint (lengths, whitespace-normalized equality, containment).

  Earlier quote-based rules, kept for reference. Up to Day 5.6 a claim was kept only if:
  1. the finding exists (it was produced by code in this run),
  2. `claim_type` matches the finding's check,
  3. the evidence record's sha256 verifies,
  4. the quote appears verbatim in that record (request line, status, a `recorded response headers: ...` line, headers, body),
  5. **entailment**: code re-checks the stored record for the issue and derives the "support lines" that show it, and the quote must lie inside one of them:

  | claim_type | record must show | support lines |
  |---|---|---|
  | header_missing | header absent from the recorded header list (XCTO: absent or not `nosniff`; XFO: also no CSP `frame-ancestors`) | `recorded response headers: ...` (+ the wrong XCTO line) |
  | cookie_flag_missing | that cookie's `set-cookie` lacks the flag | that `set-cookie: ...` line |
  | version_disclosed | `server` / `x-powered-by` contains a version number | that header line |
  | file_exposed (`.env`) | HTTP 200, non-HTML, `KEY=value` lines | the `KEY=value` lines |
  | file_exposed (`.git`) | HTTP 200 and a `ref: refs/...` or 40-hex body | the ref line |
  | file_exposed (backup) | HTTP 200, non-HTML, non-empty | the `content-type:` line and first body line |
  | directory_listing | HTTP 200 and an `Index of /` / `Directory listing for /` title | that line |
  | dns / tls | not recorded over HTTP | none (always dropped) |

  A quote that is real but does not support the claim (e.g. homepage body text for a missing header) is dropped with "quote does not support claim". If the record itself does not show the issue, the reason adds "the evidence record does not show the issue".

  Anything else is listed as "insufficient evidence" with a reason. Report text comes from fixed templates; severity comes from the finding, i.e. the table. The report shows the code-derived **supporting line** (e.g. the header list), not the model's quote.

## Evidence store: `src/evidence.py`

- SQLite (stdlib) at `data/evidence.db` (git-ignored). Each record holds an id, a UTC timestamp, a request summary (method, URL, headers sent) and a response summary (status, selected headers, body truncated to 2 KB).
- The whole summary is serialized as canonical JSON and sealed with `sha256`. `verify(id)` recomputes the hash, so any edit to a stored record is detected.
- **Redaction happens before storing** (and before truncating, so a secret is never cut in half and missed):
  - Values that look like API keys, tokens, passwords or private keys are masked to the first 3 characters + `...` + the last 2 (values shorter than 8 become `***`).
  - What is detected: `name=value` / `"name": "value"` pairs with secret-like names; `Bearer`/`Basic` credentials; known token prefixes; PEM private-key blocks (replaced entirely); long mixed letter+digit strings.
  - In `Cookie`/`Set-Cookie`, cookie values are masked but attributes (`Path`, `Secure`, `SameSite`...) are kept as evidence. `Authorization`-type headers are always masked.
- A test reads the raw DB file bytes and proves no full secret is in it.
- A `Finding`'s `Evidence` keeps a short redacted excerpt (its own sha256) plus `record_id` pointing at the full store record.

## Token budget: `src/budget.py`

`TokenBudget` tracks prompt/completion/total tokens per run (with a hard cap, default 200,000) and across all runs (`overall()`). Each call's usage is appended to `data/usage.jsonl` before the cap is checked, so even the call that breaks the cap is logged. Exceeding the cap raises `BudgetExceeded`.

## Demo app: `demo_app/app.py`

A deliberately vulnerable stdlib shop that binds to `127.0.0.1` only (any other host raises). All its secrets are fake. `--fixed` applies `FIXED_CONFIG` (security headers, cookie flags, version hidden, `/.env` and `/backup/` return 404), so a re-test can prove a fix.

## Verifier

The verifier is deterministic code, not the model. It decides whether the evidence actually shows the issue (for example: the header really is absent in the stored response). A model claim with no supporting evidence never becomes a Finding.

## Severity

Severity comes from the fixed table in `src/severity.py` (`check_id -> severity, plain rationale, remediation hint`). It is never set by a model, so the same issue always gets the same rating. An unknown check id raises `UnknownCheck`. Levels: `info`, `low`, `medium`, `high`, `critical`.

## Finding schema

See `src/models.py`:

| field | meaning |
|---|---|
| id | stable id |
| check | which check produced it |
| target | the in-scope target URL |
| location | page or path where it was found |
| severity | from the fixed table |
| evidence | 1 or more Evidence (data, sha256, UTC timestamp) |
| plain_explanation | for a non-expert owner |
| technical_explanation | for the developer |
| demo | true if produced by the mock provider or the demo app |

## Approval gate and fix-then-retest

1. The model drafts a patch (a diff or config change). It is only a proposal.
2. A human approves or rejects it. Nothing is applied without an explicit approval.
3. After the patch is applied, the **same** check re-runs against the same location.
4. The result is recorded as new evidence: "fixed" only if the verifier now passes.

## Model provider

An OpenAI-compatible chat endpoint (`config.yaml`: base_url, model, api_key_env, reasoning_effort=low, max_tokens=1500). The model always emits reasoning, so the client separates reasoning (a separate field or inline `<think>` text) from the final answer. Only the answer is used. Reasoning and the API key are never logged. The client supports `reasoning_effort`, `tools`/`tool_choice` and `response_format`, and it returns `tool_calls`, `finish_reason` and the usage dict. `MockProvider` (demo=True) can replay a script of replies for offline runs and tests.
