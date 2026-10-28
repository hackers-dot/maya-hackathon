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

`HttpClient` is the ONLY code that makes outbound HTTP requests to a scan target. (Since Web 6, two narrow, scope-checked exceptions exist for verified non-loopback targets only: the DNS-over-HTTPS lookup to one fixed resolver, and one TLS handshake with the target's own port 443. See "Web 6".) It enforces:
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
  - **The model's note is unverified text.** Restricted words (fake, scam, hack…, compliant, and "secure"/"insecure" meaning safe) are masked. "Secure" is left alone when it is the cookie attribute name: quoted, followed by "flag"/"attribute", or listed next to HttpOnly / SameSite. Every host, path or number in the note must appear in that finding's own evidence, and anything over 160 chars is too long; a note that fails is dropped, and the claim is kept.
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
  | file_exposed (`.env`) | HTTP 200, non-HTML, `KEY=value` lines | secret-looking keys first (KEY/TOKEN/SECRET/PASSWORD/API), value masked first 3 + `...` + last 2; else the first `KEY=value` line |
  | file_exposed (`.git`) | HTTP 200 and a `ref: refs/...` or 40-hex body | the ref line |
  | file_exposed (backup) | HTTP 200, non-HTML, non-empty | a SQL/archive signature line first (`CREATE TABLE`, `INSERT INTO`, zip `PK\x03\x04`), else the `content-type:` line |
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

A deliberately vulnerable stdlib shop that binds to `127.0.0.1` only (any other host raises). All its secrets are fake. Every weakness has an independent fix keyed by its check id. `--fixed` turns on all of them, and `--control FILE` (or `make_server(control_path=...)`) turns on the fixes listed in a JSON control file that the approval gate writes, so a re-test can prove a fix.

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

## Fix proposals, approval gate and fix-then-retest (Day 6)

1. **Proposals (`src/fixes.py`), from fixed templates and never model prose.** One proposal per check, referencing every finding id it addresses. Each has:
   - a kind (file block rule / header addition / config diff),
   - exact nginx and Apache snippets and a Python `http.server` note,
   - a risk level (low / medium: the chance of breaking something legitimate),
   - a rollback line.

   Covered: `.env`, `.git`, backup files, directory listing, the 3 cookie flags, the 4 headers and the server version. A new proposal is `PROPOSED` and says "proposed, not applied".
2. **Approval gate (`src/approval.py`):**
   - `PROPOSED → APPROVED` needs a non-empty approver name and records a UTC timestamp.
   - `APPROVED → APPLIED` only for the loopback demo app. The target must pass `scope.check_target` with no verified list and be loopback, so any other target is refused even if approved. A proposal must also match the target it was made for.
   - Applying adds the approved check ids to the demo app's control file (JSON, written atomically, re-read by the demo app on every request), after keeping the previous content as a backup.
   - A health check (`GET /` through `HttpClient`, expects 200) runs next. If it fails, the backup is restored and the proposals become `ROLLED_BACK`. `rollback()` restores the previous state exactly, deleting the file if it did not exist.
   - Nothing is applied without `APPROVED`.
3. **Audit log:** every transition and refusal (`approved`, `approve_refused`, `apply_refused`, `applied`, `health_ok`, `rolled_back`) is appended to `data/audit.jsonl`. Each entry stores `seq`, the previous entry's hash and its own sha256 over the canonical JSON. `verify()` reports the first broken entry, so an edited, deleted or reordered line is detected.
4. **Re-test (`src/retest.py`):** after `APPLIED`, the same checks re-run on the same paths. The status is `FIXED` only if every originating finding is now absent, otherwise `STILL_PRESENT`. It returns a before/after diff: resolved, still present, and new findings from those checks.
5. **The demo app's fixes are independent,** keyed by check id (`FIX_HEADERS`, `FIX_COOKIE`, `FIX_BLOCKED`, server version), so a partial apply leaves exactly the unapplied issues. `--fixed` turns on all of them.
6. `scripts/demo_fix_flow.py` runs the whole flow on loopback with no model: scan → proposals → approve as "demo-user" → apply → health check → re-test → before/after → audit-chain check.

## Web app (Web step 1): `src/web/`

- **Server (`server.py`):** stdlib `ThreadingHTTPServer`, bound to `127.0.0.1` only (any other host raises), default port 8090; started with `scripts/run_web.py`.
  - Requests whose `Host` header is not `127.0.0.1` or `localhost` (any port) get 400. This guards against DNS rebinding.
  - Every response, errors and 501s included, carries `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. `Server` is just `MAYA`.
  - Request bodies are always drained first (up to 1 MB), so early refusals don't reset the connection. JSON bodies are limited to 16 KB; anything else gets 415.
  - Nothing is logged per request, so nothing a user types reaches a log.
- **Static files** come only from `src/web/static/` (name pattern `[a-z0-9_-]+.(html|js|css)`, no traversal). There are no CDNs, external fonts, inline scripts, inline styles or inline handlers. `app.js` writes server strings with `textContent` only and follows only same-origin `next` paths. A test fails if any static file contains `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function` or an `http(s)://` URL.
- **Auth (`auth.py`):**
  - Email + password, hashed with `hashlib.scrypt` (n=2^14, r=8, p=1, 16-byte random salt) and checked with `hmac.compare_digest`. Passwords must be 10 to 1024 characters.
  - Users are stored in `data/web.db`.
  - Unknown accounts still cost one scrypt hash, so response time does not reveal which accounts exist.
  - **Login limit:** after 5 failed attempts for an account within 5 minutes, that account's sign-in is refused (429), even with the right password, until the window passes. Other accounts are unaffected.
  - **Sessions:** a random 256-bit token in a cookie with `HttpOnly; SameSite=Strict; Path=/`. The store is server-side, in memory, with an 8-hour expiry. Signing in replaces the session, which prevents session fixation.
  - The cookie has no `Secure` flag because the app is plain HTTP on loopback only. Add `Secure` when it is served over HTTPS.
  - **CSRF:** every POST needs the session's own CSRF token in `X-CSRF-Token` (from `GET /api/session`). An anonymous session is issued first, so sign-in and sign-up are covered too.
- **Flow:**
  - sign in / sign up → Individual or Company.
  - Company → "what do you have?": only Website works; Web app/API, Source code, Server/tool and Mobile/desktop are plain "coming soon" labels, not buttons.
  - "Connect MAYA for security checks?" Yes / No.
  - Yes → consent screen: will do read-only checks (headers, cookies, exposed files via GET, public DNS); will NOT run exploits, brute force, or change anything without approval. Allow records a timestamp.
  - Individual, "No" and a completed consent lead to the placeholder chat page ("coming next").
  - The server decides each next page, and out-of-order steps get 409.

### Web 1.5: UI and automated UI checks

- **Look:** a ChatGPT-style shell (`chat.html`) and centered cards for sign-in and onboarding, in one visual language defined by `style.css`.
  - Dark theme by default; a light theme via `[data-theme="light"]` and CSS variables.
  - `theme.js` loads synchronously in `<head>` so the saved theme applies before first paint. `localStorage` is used only inside try/catch, and `?theme=light|dark` previews a theme without saving it.
  - System font stack, gradient brand and primary buttons, visible `:focus-visible` outlines, `prefers-reduced-motion` respected.
  - Below 860 px the sidebar becomes an off-canvas drawer (hamburger button, scrim, Esc closes it). Below 560 px everything is one column.
  - Card pages use auto margins instead of `justify-content: center`, so a tall card never slides under the top corner controls.
- **Chat is not built yet.** The composer is disabled with a "Chat coming next" hint, suggestion cards only show that hint, and there is no chat endpoint, so no replies are generated or faked (a test checks this).
- **Security rules are unchanged:** no inline scripts or styles, no external URLs, `textContent` / `createElement` only. The static scan now names the exact set of expected files, so any new file must be listed and is scanned.
- **Sign-out** now issues a fresh anonymous session and returns its CSRF token, so a client can sign in again without reloading. The flow check found this.
- **`scripts/ui_flow_check.py`:** browserless flow test with `urllib`, a cookie jar and CSRF tokens. Environment proxies are disabled, redirects are not followed, and it runs against a throwaway data dir by default (or `--base` for a server started with `run_web.py --data-dir`). It covers both paths, refusals, page status and security headers on every page.
- **Test-only preview route `/__preview/<page>.html`:** serves page HTML with no session or data, only when the server is started with `ui_preview=True` (`run_web.py --ui-preview`). It returns 404 otherwise. It is an explicit flag rather than an environment variable, because the project rule is not to read environment variables.
- **`scripts/ui_screens.py`:** screenshots with the installed Chrome.
  - Headless Chrome never lays a page out narrower than about 504 px (measured: `--window-size=390` gives `innerWidth=504`), so CLI `--screenshot` shots at 390 px are cropped and wrong.
  - The script therefore drives Chrome over the DevTools protocol with a minimal stdlib WebSocket client, sets exact viewports with `Emulation.setDeviceMetricsOverride` (1280×800 and 390×844 mobile), records each page's scroll width to catch horizontal overflow, and stops Chrome afterwards.
  - Chrome uses a throwaway profile and a debug port it picks on 127.0.0.1. PNGs go to `data/ui_screens/`.

### Web 2: connect website, scans, live trace

- **Connect website (`src/web/assets.py`, `/connect-website`).** After consent, a company user enters a URL. `parse_target` validates it with the scope guard's rules: http/https only, no userinfo or backslashes, no numeric-IP spellings, no non-loopback IP literals, no single-label names, valid port. A bare domain means `https://`. Only the origin is kept.
  - **Any loopback URL is MAYA's own demo app.** The typed port is ignored and the server scans the demo app it starts itself, so a loopback URL can never aim MAYA at another local service (a database, an admin panel).
  - Other sites get a random `maya-verify-…` token. The page shows both methods (homepage meta tag; `/.well-known/maya-verify.txt` with exactly the token).
  - "Verify ownership" calls `src/ownership.py` (`HttpClient` only, only that host, only the two fixed paths) and records `verified` or `failed` with the reason.
  - Assets are stored per user in `web.db` and listed in the sidebar. The same origin added twice returns the same asset and token.
- **Scans (`src/web/runs.py`).** "Run security scan" works only for `verified` or loopback assets (otherwise 409), with one running scan per user (409). The scan runs in a background thread.
  - **Model mode:** the existing agent loop with the real provider (`reasoning_effort=low`) and the same hard caps (6 steps, 40,000-token `TokenBudget`).
  - **Deterministic-only mode:** used when the model key is not configured. Checked at scan time; the value is never read into output. Every HTTP check runs in a fixed order, labelled "Deterministic only (no model key configured)".
  - The demo app starts on demand on a free 127.0.0.1 port and stops when the web server closes.
  - Scan traffic goes through `HttpClient` with `verified={that host}` (or loopback for the demo).
- **Live trace.** `run_agent` takes a caller-owned `trace` list and appends steps as they happen. Each step carries tool, args, result summary, tokens (reasoning separate) and model-call latency. Findings land in the registry's dict, which the run shares.
  - The page polls `GET /api/runs/<id>/trace?after=N` every second. The response has new steps only, `next`, run status (running / forced final / finished / failed) and the current findings, severity-sorted from the code table.
  - When the run stops, `GET /api/runs/<id>` returns the result: findings, the model's priority order with notes labelled "unverified model text" next to the verified code evidence, dropped claims, token usage, the model that ran, the mode, and a DEMO banner for demo data.
  - The scan button is disabled while a run is in progress. Everything is rendered with `createElement` / `textContent`.
- **Authorization.** Asset and run lookups always filter by the signed-in user's id, so another user's asset or run id behaves exactly like a missing one (404). Signed-out requests get 401. Every POST still needs the CSRF token.
- **Preview data for screenshots.** `/__preview/sample.json` is a fixed sample (not from any run or account), served only in `--ui-preview` mode (404 otherwise). Preview pages render it with the same functions as real data.

### Web 3: report UI, fix flow, re-test

- **Trace grouped by model step.** One header per step carries the step's shared tokens (reasoning separate) and model latency. That step's tool calls are listed under it, and the forced final step is outlined.
- **Findings with a role toggle.** Each finding is a collapsible card. Every finding carries all three texts from the fixed templates in `src/report.py` (plain explanation + analogy + fix, technical detail, check / id / severity), plus the OWASP "mapped to" label and the code evidence line.
  - The Owner / Developer / Technical toggle only sets `data-role` on the panel; CSS shows the matching texts.
  - It never sends a request and never rewrites text, which a test checks by reading `setRole`.
  - Cards are appended, not rebuilt, so an open card stays open while the trace polls.
  - A "Fix first" panel shows the top 3 findings by severity from the code table.
- **Fix proposals panel.** Proposals come from `src/fixes.py`, built when the scan ends, before the run is marked finished. Each shows kind, risk, status (PROPOSED / APPROVED / APPLIED / ROLLED_BACK), nginx / Apache / Python snippets with Copy buttons, and the rollback line.
- **Approve / Apply / Re-test** go through the existing `ApprovalGate` and `retest()`; the web layer adds no apply logic of its own.
  - **Approve** needs an approver name (400 otherwise) and only works on PROPOSED.
  - **Apply** sends only APPROVED proposals to `gate.apply()`, which itself refuses non-loopback targets and anything not APPROVED, and audits every refusal. The button is enabled only for the demo asset with at least one APPROVED proposal; the server checks again.
  - **Re-test** re-runs the same checks on the same paths, with the run's own scope, and shows resolved / still present / new and FIXED or STILL_PRESENT. It is audited as `retested`.
  - Every action needs the CSRF token, and waits until the scan has finished (409 otherwise).
- **Per-user isolation:**
  - Each user gets their own demo app instance and control file (`data/demo/control-<user>.json`), so one user's applied fixes never change another user's demo.
  - Each user has their own hash-chained audit log (`data/audit/user-<id>.jsonl`). The audit panel shows "hash chain verified" (or the first broken entry) and the last 12 entries.
  - The token panel sums that user's scans (cap 40,000 per scan).
  - Another user's run, proposal, re-test, audit and usage are never reachable (404, or that user's own empty data).

### Web 4: chat, approve all, sorted proposals

- **Fix proposals** are sorted by severity (critical first, from the code table), then by check id.
- **"Approve all"** needs the approver name and approves every PROPOSED proposal through `ApprovalGate.approve`, one at a time, so each approval gets its own audit entry. An empty name is refused (400), and nothing is approved twice (409).
- **Chat (`src/web/chat.py`),** for Individual and Company users.
  - **Storage and access:** chats and messages are stored per user in `web.db`. `GET/POST /api/chats` and `GET /api/chats/<id>`, `POST /api/chats/<id>/messages`. Another user's chat id behaves like a missing one (404); signed-out requests get 401. Every POST needs the CSRF token.
  - **Limits:** 2,000 characters per message, 20 messages per rolling minute per user (429), and a per-user chat token budget over a rolling 24 hours (default 60,000; 429 when reached). The reply that crosses the budget is still returned, as with scans.
  - **Model call:** through the existing provider with `reasoning_effort=low` and `max_tokens=1200`. Usage, including reasoning tokens, is also recorded through `TokenBudget` (`run_id` `chat-<user>`) in `data/usage.jsonl`.
  - **What the model sees:** a fixed system prompt, then a compact list of this user's own verified findings (the latest completed scan of each of their assets: id, severity, check, location, redacted evidence line, at most 30), then at most the last 10 messages. The system prompt says:
    - MAYA is a cybersecurity assistant that explains simply, in English or Hindi/Hinglish to match the user;
    - never exploit code, attack steps against systems the user does not own, or help scanning sites the user does not own; refuse politely and explain;
    - no claims about the user's systems except from the listed findings, which must be cited by id.

    A user with no scans gets "VERIFIED FINDINGS: none …".
  - **Secrets:** user text passes through `redact_text` before it is stored or sent, so pasted keys and passwords are masked.
  - **Reply labels:** a reply is "restates verified findings" only if every sentence cites one of the user's real finding ids. Otherwise, and whenever it cites an id that is not theirs, it is "unverified model text", and unknown ids are listed. Under each reply the UI shows the label, "Model that ran" and tokens (reasoning separate). All text is rendered with `textContent`.
  - **Without a model key:** `model_configured` is false, the composer stays disabled with "Model not configured … No replies are generated", and sending returns 409 with nothing stored.
  - The send button is active only with a model and text. Suggestion cards start a chat with their prompt.
- **Robustness found by the Web 4 tests:**
  - an unknown POST path now also drains the body before its 404 (Windows connection resets);
  - any unexpected handler error returns a plain `500 internal error` instead of dropping the connection;
  - the sentence splitter no longer treats the dot in `/.env` as a sentence end.

### Web 5: chat fix status, evidence pack, downloads, eval

- **Chat knows fix status.** Each finding in the chat context carries the status from the user's latest run of that asset: `FIXED` only when a re-test showed it gone, `APPLIED` when its fix was applied but not (or not successfully) re-tested, otherwise `DETECTED`. System prompt rule 5: never describe a FIXED finding as still present; say it was fixed and re-tested, and cite its id. Statuses come only from that user's own runs.
- **Audit entries carry the run id.** `ApprovalGate.approve/apply/rollback` accept `run=`; the web app passes its run id, so a pack can pick exactly that run's approve / apply / health / re-test entries out of the user's audit log.
- **Evidence pack (`src/evidence_pack.py`).**
  - `artifacts/<record id>.json`: each evidence record byte-for-byte as stored (already redacted); its sha256 equals the store's seal, and a record whose seal fails is not packed.
  - `findings.json`: code-detected findings, severity from the fixed table, plus status.
  - `manifest.json`: per file sha256, size, UTC time, check ids, finding ids, tool, model (if any) and the demo flag; a DEMO marker when any finding is from the demo app (also in `README.txt`).
  - `custody_log.jsonl`: hash-chained who / what / when (same hashing as the audit log): `pack_started`, one `evidence_captured` per artifact, the run's audit entries (`audit:approved`, `audit:applied`, `audit:health_ok`, `audit:retested`, with their original hashes), `findings_written`, and last `manifest_sealed` with the manifest's sha256.
  - `README.txt` says: "Tamper-evident, not tamper-proof (unsigned). Provenance is self-reported. Designed to support review; not legally validated."
  - `verify_pack()` recomputes every hash and the chain and names the failing file or log line (changed byte, missing or unlisted file, edited / deleted / reordered / truncated custody line, edited manifest). `verify_zip()` refuses absolute, drive-letter and `..` member names before extracting anything, then verifies the extracted copy.
- **Downloads.** `GET /api/runs/<id>/evidence.zip` (built fresh and re-verified with `verify_zip` before every download; a failure is a 500, never a bad file), `report.html` and `report.md` (the existing `src/report.py` builder, with the run's verifier output). Signed-in only, another user's run is 404, `Content-Disposition: attachment`, security headers as on every response. The result view has "Download evidence pack" and "Download report" buttons (hrefs set with `setAttribute`, text from the HTML).
- **Eval (`src/eval/harness.py`, `eval/golden/`).** Three golden states of the demo app via its control file: vulnerable (11 findings), fully fixed (none), partially fixed (only `.env`). Five scripted model levels: perfect, sloppy, adversarial, truncated JSON, no final answer. The real scanner, agent loop, verifier and report builder run; only the model is scripted. Per case and level: code precision / recall / false positives, claims kept and dropped with reasons, **fake claims that reached the report** (a report claim whose finding is not in the golden case, whose claim type or severity disagrees with code, or whose note carries a planted path or host), notes dropped, and whether a report was produced. `python -m src.eval.harness --write` writes `docs/eval_results.md` and `docs/eval_results.json`. The agent loop takes an injectable `verifier`, used only by a test with a deliberately leaky verifier, which makes the fake-claim metric nonzero (so the metric can fail).
- **Live eval (`scripts/eval_live.py`).** The user runs it: N=3 scans of the vulnerable demo app with the real model, TokenBudget 40,000 per scan, and per run findings, claims kept / dropped, fake claims in report, tokens and model latency. `--write-doc` appends a dated, model-labelled table under "Live runs" and is refused with `--dry-run`, so scripted numbers are never recorded as live. Until then the doc says "not yet measured".
- **Quality panel.** `GET /api/eval` returns a compact view of `docs/eval_results.json`, or `{"available": false}` when the file is missing or unreadable; the panel then says "run the eval". No number on the panel comes from anywhere else.

### Web 5.1: demo reset, chat history, pack demo marker

- **Reset demo app.** `POST /api/assets/<id>/reset-demo` (CSRF; the user's own demo asset only, any other asset is 404 or "only MAYA's demo app can be reset"; refused while that user has a scan running). `ApprovalGate.reset_demo()` backs up the user's control file the same way `apply` does (so `rollback()` can restore it), writes `{"fixes": []}`, and audits `demo_reset` with the fixes that were on. Each user has their own control file, so no other user's demo app changes. The "Reset demo app" button shows only for the demo asset.
- **Chat scan history.** The chat context lists the user's last 3 completed scans (run id, UTC date, findings count, status counts). If the latest scan found nothing, it says "latest scan: no issues detected in the checks run" and lists the earlier runs' FIXED finding ids. Those ids also count as known citations for reply labels. System prompt rule 6: when a history is listed, never say no scan was done, and never call the site secure. With no scans at all, the context is unchanged ("none … has not scanned"). "Latest" breaks ties in `started_at` by start order.
- **Pack demo marker from the asset.** `build_pack(demo=...)` is set from the run's asset (the loopback demo app), so a run with 0 findings is still marked DEMO. With no findings, README.txt says "No issues detected in the checks run."
- **`scripts/demo_prepare.py --reset`** prints the demo click order (reset, scan, approve all, apply, re-test, download pack, chat question). It only prints.

### Web 6: chat accuracy, DNS and TLS checks, JS libraries

- **Chat accuracy.** Each scan-history line now reads "scan found N findings; later re-test: all fixed / M still open / no re-test", plus the status counts. Prompt rule 7: never say a scan found nothing when it found N > 0, and describe the re-test outcome separately. Chat citations may contain a space, so DNS ids such as `[missing_spf:TXT shop.example]` count as citations.
- **Which checks apply.** `runner.not_applicable(check, base)` returns "not applicable (loopback demo)" for the DNS and TLS checks on loopback, and "not applicable (target is not HTTPS)" for TLS on an http target. `runnable_checks(base)` is what the agent sees: `ToolRegistry` builds its tool enum and `system_prompt(runnable)` per target, so DNS and TLS are offered to the model only for a verified non-loopback target. Every result carries `checks` (ran / not run / could not run / not applicable), which the result view lists under "Checks".
- **SPF / DMARC (`checks_dns.run_dns_check`).** The scope guard must accept the domain (verified) and it must not be loopback, before any request. Then a GET to `https://cloudflare-dns.com/dns-query?name=<domain>&type=TXT` (and `_dmarc.<domain>`) with `accept: application/dns-json`, a 5 s timeout, no redirects and `trust_env=False`. The raw JSON answer is stored (redacted) as the evidence record. The existing pure parsers decide; severity comes from the table. **The domain name is sent to that DNS-over-HTTPS provider**, which the connect page and README say. If the lookup fails, the check "could not run" (no finding is invented).
- **TLS (`checks_tls`).** `scope.check_target` runs before any socket. One handshake with port 443 of the verified host reads the certificate without trusting it (an expired or self-signed certificate must still be readable), then the connection closes. Findings get their own table rows so severity stays a fixed lookup: `cert_expires_within_14_days` (high; also already expired), `cert_expires_within_30_days` (medium), `tls_protocol_below_1_2` (medium). Evidence: not_after, protocol and issuer, stored as a record. The verifier re-judges the claim against the record's capture time.
- **Outdated JS libraries (`checks_js`).** `<script src>` names such as `jquery-1.12.4.min.js`, `jquery@1.12.4/...` or `.../jquery/1.12.4/...` are compared to a SHORT EXAMPLE table (`OLD_BEFORE`). Wording is always "appears outdated". Severity medium, OWASP mapped to A06:2021 (its official category name). Proposal kind "dependency update". The demo app's homepage now loads `jquery-1.12.4.min.js`, and its `outdated_js_library` fix switches it to `jquery-3.7.1.min.js`, so the vulnerable demo has 12 findings.
- **Re-test.** It re-runs the check that produced each finding (TLS findings re-run the TLS check). A check that cannot run now keeps its findings "still present": nothing is called fixed without proof.
- **Tests without outbound network.** DoH answers come from `httpx.MockTransport`. TLS runs against a loopback TLS server with certificates made by the `openssl` command line at test time (tests skip if it is not installed), including a real TLS 1.1 handshake.

### Web 7: code connector, logs upload, data delete

- **Uploads are assets with a finished run.** `POST /api/uploads/code` (ZIP, at most 10 MB) and `POST /api/uploads/logs` (`.log`/`.txt`, at most 5 MB) take the raw file as the body: CSRF header, an allowed `Content-Type`, and `X-Upload-Name` (a plain file name with the right extension). Company users only, after consent, one at a time per user. A body a little over the cap is drained and refused with 413; anything larger closes the connection. Each upload becomes an asset of kind `code` or `logs` (only its name is kept) and a finished run (`RunManager.record_upload`). So the findings table, report download, evidence pack, chat context and per-user 404s all work unchanged. Apply and re-test are refused for uploads.
- **Code connector (`src/code_scan.py`).** The ZIP is read in memory with `zipfile` and never extracted to disk, imported or executed. Before any member is read, the whole upload is refused for a `..`, absolute, drive-letter or backslash name, a symbolic link, an encrypted member, more than 2000 entries, or more than 50 MB declared. While reading: a per-member ratio guard (over 100:1 is treated as a zip bomb), at most 2 MB per file and 50 MB in total actually read (declared sizes are not trusted). Files with NUL bytes or non-UTF-8 content are skipped as binary.
  - Checks are pure functions over (path, text): hard-coded secrets (name = value, well-known token prefixes, private-key blocks; references such as `os.environ[...]` and placeholders are ignored), a committed `.env` (templates such as `.env.example` are not), `debug=True`, `eval(`/`exec(`, `pickle.load(s)`, `shell=True`, MD5/SHA-1 near passwords, and pinned dependency versions older than a SHORT EXAMPLE table (`requirements.txt`, `package.json`).
  - Evidence is `path:line` plus a masked excerpt. A secret becomes first 3 + "..." + last 2 before it reaches a finding or the evidence store; a test checks the full value is in neither, nor in the pack. Wording is "appears" / "detected". OWASP labels are "mapped to" (A07, A05, A03, A08, A02, A06).
- **Logs (`src/logs.py`).** Parsers for nginx/Apache combined access logs and syslog sshd lines: at most 100,000 lines, 2000 characters each. Detections are grouped by source address and labelled "shares source address", never as one actor:
  - a burst of failed logins (10 within 5 minutes by default), including POSTs to login paths answered 401/403;
  - requests for `.env`, `.git`, `wp-login.php`, `phpmyadmin`;
  - many 4xx responses (20);
  - SQL-injection-like or traversal strings in the request line (detection only; it does not show success);
  - a successful login after 3 or more failures.

  These thresholds are configurable heuristics, not a standard. Evidence lines are verbatim with their line numbers; only credentials inside a line are masked, and IP addresses stay visible. Log detections have no OWASP category: they describe activity, not a weakness in the app.
- **Optional model summary.** `POST /api/runs/<id>/summarize` (log runs only) sends the finding list to the existing provider (low effort, 1200 max tokens, TokenBudget 40,000). The model returns typed claims `{finding_id, claim_type: "log_activity", priority, note}` and never quotes. The same `verify()` as scans keeps a claim only if its finding exists, its evidence record's hash verifies, and the stored lines still show the detection (`logs.support_lines`); code attaches the evidence line. Severity comes from the table, a model-supplied field makes the claim malformed, and notes that name addresses or paths outside that finding's evidence are dropped. Without a key the answer is 409 "Model not configured".
- **Evidence owner.** `EvidenceStore(owner="user-<id>")` tags each record; older databases get the column added in place. The owner is not part of the sealed summary or its hash. Evidence packs name the capture tool per record (HTTP client, code connector, log parser, TLS check).
- **Disconnect and Delete my data.**
  - `POST /api/assets/<id>/disconnect` deletes the asset row and, if no other verified asset uses that host, its entry in `verified_targets.json`.
  - `POST /api/data/delete {"confirm": "DELETE"}` (CSRF; refused while a scan runs) deletes the user's assets (and verified status), runs, evidence records (by owner), chats and messages, and the demo app's control file. It replaces the user's audit log with one entry, `user_data_deleted`, holding counts only.
  - The account stays. Uploads were never stored, and packs are built on demand, so neither has anything left to delete. Token usage lines in `usage.jsonl` hold no content and are kept.
  - Another user's data is untouched (tested).
  - The data-use note (what is stored, where, how long) is on the "Your data" page, and a short version is on the consent page.
- **Dashboard.** The sidebar lists connectors: Website, Source code ZIP and Logs work; API, Mobile and Cloud are labelled "coming soon" and are not buttons.

### Web 8: suspicious message check

- **What it is.** The user uploads a screenshot (PNG/JPEG, at most 5 MB; the type comes from the magic bytes, never the file name) of a message, ad or page that claims to be from their business. `POST /api/uploads/image` needs `X-Confirm-Send: yes`, matching the UI checkbox "I understand this image will be sent to the model provider". Without it the image is never sent. Without a key the answer is 409 "Model not configured" and nothing is stored.
- **The model reads; code decides (`src/brand_check.py`).**
  - The image goes to the existing provider as an `image_url` data URL, with a strict `json_schema` response format: `{urls, phone_numbers, brand_names, payment_requests, urgency_phrases}`, lists of strings of at most 200 characters, copied from visible text.
  - `parse_extraction` enforces the schema again in code: unknown fields, non-lists, over-long and extra items are dropped and listed. Every item passes through `redact_text`, so a password visible in the screenshot is masked.
  - Code normalises domains (scheme, `www.`, port, path, IDNA) and phone numbers, then compares them with the user's verified websites plus an optional per-user official-domains and official-numbers list (`official_entries` table, shown masked).
  - A fixed lookalike rule flags "resembles your domain" for: the same name with another ending; an extra or missing hyphen; look-alike characters (digits, `rn`/`m`, Cyrillic letters, punycode); your name plus a keyword; your name as a subdomain of another domain; or 1 to 2 characters different (names of 4+ letters).
  - Everything else is "not in your list, needs review". Payment requests and urgency phrases are counted, not judged.
  - Severity comes from the table. Wording never says fake, scam or fraud, and a test checks every text.
  - Each item is shown with "read by the model from the image (unverified)" beside the code verdict.
- **Passive only.** No request is ever sent to an extracted URL. A test records every outbound request during an upload (none) and every socket connection (loopback only). The optional "Check DNS" (`POST /api/runs/<id>/dns-check {domain, confirm: true}`) works only for a domain read from that image. It asks the existing DNS-over-HTTPS resolver for an A record via `doh_query` and never contacts the domain; the UI says the domain name goes to cloudflare-dns.com.
- **Evidence.** One evidence record holds the image hash prefix, size, type and the masked extraction (never pixels). The store's redaction masks long hex strings, so the pack also always gets `image/image_info.json` with the full SHA-256, size and type. The image file is added only if the user ticked "include image": it is held in memory with the run and deleted with "Delete my data". Attachments are hashed in the manifest and logged in the custody log (`attachment_included`).
- **Report notes.** Fixed templates for a domain registrar and for a platform, with placeholders such as `[your business name]` and the image hash. `POST /api/runs/<id>/notes` drafts one with status DRAFT. Only `ApprovalGate.approve_note` with an approver name makes it READY (audited `note_approved`). There is no sending code anywhere; a test checks for mail libraries.

## Model provider

An OpenAI-compatible chat endpoint (`config.yaml`: base_url, model, api_key_env, reasoning_effort=low, max_tokens=1500). The model always emits reasoning, so the client separates reasoning (a separate field or inline `<think>` text) from the final answer. Only the answer is used. Reasoning and the API key are never logged. The client supports `reasoning_effort`, `tools`/`tool_choice` and `response_format`, and it returns `tool_calls`, `finish_reason` and the usage dict. `MockProvider` (demo=True) can replay a script of replies for offline runs and tests.
