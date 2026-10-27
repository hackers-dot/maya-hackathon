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
  - its exact hostname is in the verified-targets list (empty by default).
- The guard refuses ambiguous URLs outright: userinfo (`http://127.0.0.1@evil.com`), backslashes, non-http(s) schemes, and non-canonical IP forms (decimal `2130706433`, hex `0x7f000001`, short `127.1`).
- Ownership verification (planned): MAYA issues a random token, and the user publishes it at `/.well-known/maya-verify.txt` or in a DNS TXT record. Only after MAYA reads the token back is the host added to the verified list.

## Single egress point: `src/scanner/http_client.py`

`HttpClient` is the ONLY code that makes outbound HTTP for scanning. It enforces:
- `scope.check(url)` before every request, **including every redirect hop**. Redirects are followed manually (max 5), so a redirect to an out-of-scope host is refused before it is requested.
- Only GET and HEAD. Any other method raises `MethodNotAllowed` before anything is sent.
- 5 s timeout, at most 1 request per 0.2 s, a fixed User-Agent `MAYA-scanner/0.1 (authorized security check)`.
- `trust_env=False`, so no proxy from the environment can reroute scan traffic.
- At most 256 KB of a response body is read.
- Every response is written to the evidence store, and the response carries its `evidence_id`.

## Tools (checks)

Checks are read-only, non-destructive observations. No exploit code, no attack payloads, no brute force.
- `src/scanner/checks_headers.py`: pure functions over a `Response` (no network). They cover missing CSP/HSTS/X-Frame-Options/X-Content-Type-Options, cookie flags (Secure/HttpOnly/SameSite), and version disclosure in `Server`/`X-Powered-By`. Each `Finding` carries the check id, location (URL path), severity from the table, and the evidence record id.

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

An OpenAI-compatible chat endpoint (`config.yaml`). The model always emits reasoning, so the client separates reasoning (a separate field or inline `<think>` text) from the final answer. Only the answer is used. Reasoning and the API key are never logged. `MockProvider` (demo=True) is used for offline runs and tests.
