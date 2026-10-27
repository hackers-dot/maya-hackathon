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

## Tools

Tools are read-only, non-destructive checks (for example: missing security headers, cookie flags, exposed directory listing, TLS config). Rules:
- No exploit code, no attack payloads, no brute force. A check observes; it does not attack.
- Each tool calls `scope.check_target()` before any request, and refused targets raise an error.
- Request rate is limited per target.

## Evidence store

- Each observation (response headers, a status line, a body excerpt) becomes `Evidence`: description, data, `sha256(data)` and a UTC timestamp.
- The model validator checks that the hash matches the data, so evidence cannot be edited silently.
- Stored under `data/` (git-ignored) as JSON, keyed by finding id.

## Verifier

The verifier is deterministic code, not the model. It decides whether the evidence actually shows the issue (for example: the header really is absent in the stored response). A model claim with no supporting evidence never becomes a Finding.

## Severity

Severity comes from a fixed table in code (`check -> severity`). It is never set by the model, so the same issue always gets the same rating. Levels: `info`, `low`, `medium`, `high`, `critical`. (The table lands together with the first real check.)

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
