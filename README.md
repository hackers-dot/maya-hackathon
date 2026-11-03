# MAYA

MAYA is an AI security agent for sites and apps you own. You connect a target, prove you own it, and MAYA runs safe, non-destructive checks. It produces an evidence-backed report: which vulnerability, on which page, at what severity, with proof. It explains each finding in plain language and proposes a patch that a human must approve. After the patch is applied, MAYA re-runs the same check to prove the issue is fixed.

All code in this repository was written during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026). A private prototype existed before the event, but none of its code was reused here.

Built with AI coding assistance; design, decisions and testing by the author.

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

## Demo

Video: <link added at submission>

To reproduce the demo in the browser (no model key needed; with `BASETEN_API_KEY` set the scan is model-guided):

1. `.venv\Scripts\python scripts\run_web.py`, then open http://127.0.0.1:8090.
2. Create account → Company → Website → Yes, continue → Allow.
3. "Use the demo app" → Continue → in the sidebar, "Demo app".
4. More → Reset demo app (puts the practice shop back in its broken state).
5. Run security scan, and wait for the score card.
6. Type an approver name → Approve all → Apply approved fixes → Re-test.
7. Export → Download evidence pack.

`demo_fix_flow.py` does steps 4-6 in the terminal; `demo_prepare.py --reset` prints this click order.

## Status

"Tested live" means run for real against the real model or a real third-party service. Everything else ran on the loopback demo app, on this repository's own files, or against faked services in the tests.

| Feature | Status | Note |
|---|---|---|
| Scan agent with the Meta model (`meta-models/muse-spark-open-1026` on Baseten) | Built and tested live | 9 live runs on the demo app (see Evaluation) |
| Verifier (model claims checked against code findings) | Built and tested live | 9 live runs and 15 scripted runs |
| Website scanner (19 checks: headers, cookies, exposed files, JS libraries, version disclosure) | Built, tested on the demo app or fakes only | See "Real-site test" |
| Real-site ownership verification (meta tag, file, DNS TXT) | Built, tested on the demo app or fakes only | See "Real-site test" |
| DNS checks (SPF, DMARC) and TLS check | Built, tested on the demo app or fakes only | They do not apply to the loopback demo |
| Fix proposals, approval gate, apply, re-test | Built, tested on the demo app or fakes only | By design, apply only ever changes the demo app |
| Evidence pack, audit log, score, scan diff, exports, reminders | Built, tested on the demo app or fakes only | |
| Chat (answers from your own verified findings) | Built, tested on the demo app or fakes only | Without a key it says "Model not configured" |
| Investigation agent (Investigate in the chat) | Built and tested live | Run once, not a general accuracy claim |
| Source code ZIP and log uploads | Built, tested on the demo app or fakes only | Also run on this repository's own files |
| GitHub connector | Built and tested live | Run once, not a general accuracy claim |
| Suspicious message check (vision) | Built and tested live | Run once, not a general accuracy claim |
| Lookalike watch | Built and tested live | Run once, not a general accuracy claim |
| Model comparison script (`scripts/compare_models.py`) | Built, tested on the demo app or fakes only | Not run live |
| Guide pet (hover guide, tour, Calm mode) | Built, tested on the demo app or fakes only | Real mouse and keyboard events in headless Chrome |
| Voice sound quality | Built, tested on the demo app or fakes only | Not tested by ear |
| Logo eye blink | Built, tested on the demo app or fakes only | Sampled every 20 ms in headless Chrome |
| Ollama (local model) fallback | Not built | |
| DNS-rebinding pinning for real targets | Not built | See Limits |

## Live runs during development (run once, not a general accuracy claim)

| What | When it ran | Recorded figures |
|---|---|---|
| Suspicious message check | One synthetic test screenshot | 1 model call, HTTP 200 in 6.65 s, 1,007 tokens (353 reasoning); 7 items read, 0 dropped; 2 findings by code (a look-alike domain, high; a payment request, medium) |
| Lookalike watch | `example.com` | 81 requests, only to cloudflare-dns.com (50, all HTTP 200) and crt.sh (31: 3 answered, 7 HTTP 502, 21 timeouts); 306.9 s; the lookalike domains themselves were never contacted |
| GitHub connector | This repository | 1 request to codeload.github.com, HTTP 200 in 1.39 s, 339,776 bytes; 122 entries, 107 scanned; 75 findings, mostly pattern text in the scanner's own source and tests (11 after the false-positive fixes of Web 10.1) |
| Investigation agent | The demo app's results | 2 model calls (2.08 s and 3.87 s), 3,493 tokens; 10 claims kept, 0 dropped |

## Evaluation

0 fake claims reached a report in 15 scripted adversarial runs and in 9 live runs (3 runs x 3 sets). The scripted runs are 3 demo-app states x 5 model behaviours (perfect, sloppy, adversarial, truncated, no answer). The live runs used the Meta model on the vulnerable demo app, with a 40,000-token cap per scan. Details are in [docs/eval_results.md](docs/eval_results.md).

All 9 live runs ended with the stop reason `forced_final`. This is the designed path, not a failure: the loop reserves its last step for the final answer. On that step, or as soon as every runnable check has been tried, it asks the model for its final claims with tools switched off. The report is built from the code's findings either way, so it never depends on the model finishing.

## What data leaves your computer, and when

| When | What leaves | Where it goes |
|---|---|---|
| Sign-up, onboarding, consent, results, approvals, exports, Delete my data | Nothing | Stays on this computer |
| Verify ownership of a real site | GET requests for `/` and `/.well-known/maya-verify.txt` | Your site |
| Verify by DNS TXT | Your domain name | cloudflare-dns.com |
| Scan | Read-only GET/HEAD requests | The target (your verified site, or the local demo app) |
| Scan of a verified real site | Your domain name; one TLS handshake | cloudflare-dns.com (SPF, DMARC); your site on port 443 |
| Scan with a model key | Masked check results and the model's tool calls | The model provider (Baseten) |
| Chat or Investigate | Your question and your own verified findings | The model provider (Baseten) |
| Suspicious message | The screenshot, only after you tick the box; optionally a domain name | The model provider; cloudflare-dns.com |
| GitHub connector | The repository name | codeload.github.com |
| Lookalike watch | The candidate domain names | cloudflare-dns.com and crt.sh |
| The pet speaking | The spoken text, only if the browser uses an online voice | That voice's provider |

## Limits

- The server listens only on 127.0.0.1 and uses plain HTTP, so the session cookie has no `Secure` flag.
- Sessions are kept in memory: restarting the server signs everyone out.
- DNS rebinding (a verified name that later resolves to an internal address) is not handled for real targets.
- There is one model provider. If it is down or out of credits while a key is set, a scan fails with "model call failed" (there is no automatic fallback). With no key set, scans run in Deterministic-only mode and the chat says "Model not configured".
- When the operating system asks for reduced motion (on Windows: Animation effects off), the logo does not blink and the pet does not walk or play.
- The pet's voice depends on the voices installed in the browser, and speech starts only after your first click or key press.

## Guide pet

MAYA, the project's own character, lives on every signed-in page as a small pet: a white cat-ear helmet, a black visor face screen, a green suit and a shield "M" emblem. She guides you one step at a time, shows how things are going on her face, and can speak.
- **Small and out of the way:** about 80 px tall (56 px on phones). What she says appears in a small bubble beside her, with choices as chips ("Show me", "Next tip", your level), and it hides after you have had time to read it. The bubble never covers the control she is talking about and tries not to cover any other control nearby. On her own surface there are only two buttons, mute and hide; "Call pet" and "Pet settings" are in the sidebar.
- **Pet settings** (sidebar): experience level, what you run (shop, company, clinic, developer or something else), voice picker with Preview, speed, pitch, speak the steps, speak answers, microphone (optional), "Let the pet help me" (not available yet) and "Reset what the pet learned".
- **Calm mode** (Pet settings, off by default, saved in this browser only): the pet stays in its corner and does no idle antics; its bubble, speech and blinking stay. On the sign-in page the big hero is the one visible mascot; the live pet keeps only its bubble and its mute and hide buttons there.
- **What she does:** she walks to the button she is talking about and points at it without covering any other control, then goes home. You can drag her anywhere. With reduced motion turned on she stays docked and only her face changes.
- **Hover guide, on every page (sign-in too):** rest the mouse on a control for a moment and she glides next to it, then shows and says what it is, what a click does, and where the data goes: "only reads", "changes your practice demo app", "needs your approval", "sends ... to ..." (naming where), or "deletes your data".
  - Every interactive control on every screen has a fixed entry with a stable `data-guide` id (checked in headless Chrome over all screens). Only dynamic items (a finding, a chat, a trace step) use a sentence built from their clean name, e.g. "This is the finding for Configuration file appears publicly readable. Open it to see the evidence and the fix."
  - The first hover on a control gives the full line (two or three short sentences); later hovers give one sentence. The spoken text is the bubble text. She does not repeat the same control within 30 s and stops talking when you move away.
  - After a click she says what happened, e.g. the Website connector: "This is where you connect a website you own. First I'll ask you to prove it's yours, then I run read-only checks."
  - Not on touch screens and not while you type.
- **She learns, on this device only:** she remembers which buttons you have used. New ones get the full explanation, familiar ones a single line or nothing. When you are idle she may suggest one thing you have not tried yet. "Reset what the pet learned" and "Delete my data" clear it. Nothing is sent to MAYA's server.
- **Work and play:** while a scan or re-test runs, an approval is waiting, you are typing, or the pointer was on a control in the last 6 s, she stays calm and helpful.
  - After 8 s of quiet she looks around or waves, then does one small thing every 15 to 25 s (wink, wave, a tiny dance, look at the pointer, peek, stretch).
  - She gets sleepy at about 45 s and wakes up surprised when you move the mouse.
  - With reduced motion she does not walk or dance; she still blinks, winks and gets sleepy.
- **After a scan:** she reacts to the real result and offers a short tour ("Show me" or "Skip"): the score ring, then each finding in severity order (critical first) with what it means and what to look at, then Fix first, then the approve step. She explains approving but never does it; Esc ends the tour.
- **"Let the pet help me"** (Pet settings, off by default): after you read and accept a short list, she may do a few harmless things herself.
  - **What she may do:** scroll to what she is talking about, open or close a finding card, switch the Owner / Developer / Technical view or the summary language, highlight something, or open a read-only panel.
  - **What she never presses:** Approve, Approve all, Apply, Re-test, Re-scan, Run scan, Reset demo app, Allow, Verify ownership, Delete my data, Disconnect or Sign out. She never uploads, edits lists or reminders, types your name or fills in a form. For those she shows a chip such as "Run the scan?" that only brings you to the button; you press it.
  - **Stopping and the log:** Esc or any click of your own stops her. Everything she did, or refused to do, is listed in Pet settings.
- **The pet cannot approve or apply anything.** That is enforced in code and in the tests, not only described here.
- **Steps and emotions are not from a model.** They come from a fixed state machine over your own stored data: account, consent, verification, scan, findings, proposals, apply, re-test.
  - She explains what was found before she asks for any approval. She never approves or applies anything for you, never says a site is "safe" or "secure", and gives no time or cost estimates.
  - Her greeting and guide lines are fixed templates, so they carry no "unverified model text" badge. Only real model answers in the chat carry it.
- **First visit:** on a new device or account she waves, says hello ("Hi! I'm MAYA...") and asks your level, then what you run, with chips. Choosing makes her happy and the guide continues at once in those words.
- **English only for now**, written as MAYA herself (first person, female). The language switch is hidden.
- **Audience-aware lines:** the guide steps, the finding lines, the tour, the approval and the re-test lines are fixed templates for every level (new, some experience, technical) and every audience (shop or small business, company or team, clinic or healthcare practice, developer, something else). A shop hears about customers and checkout pages, a developer hears CSP, HSTS, OWASP and the evidence. You can change both in Pet settings.
  - **Clinics:** security guidance about the website only. She gives no medical advice and makes no compliance or privacy-law claims.
  - No line says a site is safe, secure, protected, compliant or hacked, none guarantees anything, and none gives a time, a cost or a number that is not in your results (tested over every line).
- **Voice is your browser's built-in text-to-speech, not a Meta model, and not MAYA's server.**
  - **She speaks from the start, and mute is the only switch that silences her.** The speaker button on the pet mutes her and the choice is remembered. Muted, she keeps showing the bubble, moving her mouth and guiding; only the sound stops. Pet settings also has per-kind switches (guide lines, hover explanations, answers), all on by default.
  - **Browsers start speech only after your first click, key press or touch on the page.** Until then the bubble shows "Click anywhere and I'll start talking". On that first gesture she unlocks the audio and says the line that was waiting: only the latest one, never a backlog.
  - By default she picks a female-sounding English voice (en-IN first, then other English) and never a male-sounding one. The default pitch is slightly high (1.2). You can pick any installed voice yourself.
  - If no female-sounding voice is installed, the settings page says so; install one in Windows Settings > Speech.
  - Some browser voices are online voices: the browser may send the spoken text to the voice provider. MAYA itself sends nothing.
  - She speaks only a cleaned summary (no ids, paths, code or masked values). The microphone is optional; your browser may send the audio to its speech provider.
- **Checked here:** headless Chrome on this Windows machine found 24 voices, including Microsoft Heera (en-IN, female, used by default) and Zira (en-US, female). A test sentence fired start, word-boundary and end events.
- **Not tested live:** how the voices actually sound, the microphone, the feel of the animations and dragging by hand.

## Visual design

MAYA uses one visual language across all screens: a deep black-to-emerald background, emerald and cyan accents, and translucent cards. The text on bright buttons and severity badges is always dark. Severity has one colour per level everywhere: critical red, high orange, medium amber, low blue, info gray. Every text and badge colour pair is checked for contrast (`scripts/contrast_check.py`; at least 4.5:1, or 3:1 for borders and large text). The effects are plain CSS: two corner glows, a faint dot grid kept away from the centre, and one slow wave layer that pauses when the tab is hidden and stops under reduced motion. Only the sidebar and the centered form card are blurred. The app uses system fonts and no external assets, and the light theme follows the same rules.

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
- **2026-10-28 (Web 10):** GitHub connector, lookalike watch, model comparison.
  - "GitHub repo": paste a public repository address. MAYA downloads its ZIP from codeload.github.com (no token, at most 25 MB, redirects only to codeload), checks it in memory with the same code checks as a ZIP upload, and never writes or runs it. The repository name is sent to GitHub. One fetch per 10 seconds.
  - "Lookalike watch" for your own domain: up to 40 lookalike names (typos, look-alike characters including punycode, hyphens, other endings, your name plus words like "support" or "login"). The top 25 are looked up only through cloudflare-dns.com and crt.sh (the names are sent there); MAYA never contacts the lookalike domains themselves.
  - Findings: recent certificate (high), DNS records (medium), no records (info). Failed lookups stay "unknown", and shared IPs or nameservers are grouped without claiming who runs them. Registrar and hosting note drafts need a named approval, and an optional model summary is verified like scans.
  - `scripts/compare_models.py` compares a second model provider, once one is configured in `config.yaml`. It makes real model calls and has not been run live.
  - Offline eval not regenerated: finding and claim counts are unchanged (checked row by row).
- **2026-10-28 (Web 10.1):** Fewer scanner false positives, crt.sh time budget.
  - The code connector no longer flags numbers (`TOKEN_CAP = 40000`), environment-variable names, identifiers, or eval/exec/pickle/shell/MD5/debug text inside strings and comments. Python files are read with the standard `tokenize` module; JavaScript strings and comments are stripped first.
  - Findings in tests, fixtures, demo and docs folders are labelled "test or demo code", one severity level lower, in their own report section.
  - On this repository's own files: 75 findings before, 11 now. Real cases are still detected (tested).
  - The lookalike watch gives crt.sh a 5 s timeout, 1 retry and a 90 s total budget (then "certificate lookup skipped (time budget)"; DNS lookups always finish), and shows progress per candidate.
- **2026-10-28 (Web 11):** Investigation agent, owner summary, client report, reminders.
  - "Investigate" in the chat: a separate, bounded agent (6 steps, token budget) with read-only tools over your own data (findings, what changed, score history, fix status, check explanations, lookalike watch). Claims that cite anything that is not yours are removed, and the tool calls are shown under the reply. The scan agent is unchanged.
  - "Summary for the owner": score, counts, the top 3 fixes with their change risk, what is fixed and what could not run, in English or Hinglish (the Hinglish needs native-speaker review). It has no time or cost estimates; an optional model reword is dropped if it adds a number, a time or cost word, or a reassurance.
  - "Download client report": one self-contained HTML file to share by hand (masked evidence, fixes, what changed, re-test, the evidence pack's manifest hash and its disclaimer). Print or save it as PDF from the browser.
  - Re-scan reminders in 7, 14 or 30 days: "re-scan due" when overdue, and an .ics file for your calendar. Nothing runs by itself.
  - Offline eval not regenerated: finding and claim counts are unchanged (checked row by row).
- **2026-11-02 (Visual polish):** Design system, score ring, trace timeline, chat bubbles. No new features, no behavior changes. (Historical; the indigo accent and `favicon.svg` below were superseded by the visual v2 tokens and the brand commit.)
  - Design tokens in CSS variables: one indigo-to-cyan accent, a 4/8/12/16/24 px spacing scale, a radius scale, soft shadows, severity colors and a type scale. System fonts only, no external assets.
  - Sign in: a two-column hero (stacks on phones) on a slowly moving gradient that stops under reduced motion.
  - Results: a score card with a circular grade ring (SVG built in code) and severity chips, "Fix first" as three numbered cards, finding cards with shaped severity badges and an evidence block, a timeline trace with a pulsing running step, before/after rings for the re-test, and a Proposed / Approved / Applied / Re-tested progress bar.
  - Chat: message bubbles, citation chips, a muted "unverified model text" badge, a typing indicator. Sidebar icons, an active-item highlight, empty states, loading skeletons, toasts, and a favicon served as /static/favicon.svg.
  - Contrast (WCAG ratio), dark theme: body text on the page 16.1, on panels 14.9; muted text 8.2 on panels; captions 5.4 on the darkest panels. Light theme: body text 17.7, muted 7.7, captions 5.1, status green 6.4. Every severity badge is at least 5.2.
  - Offline eval not regenerated: no finding or claim logic changed.
- **2026-11-02 (Guide pet):** Mascot, guided steps, optional speech.
  - `src/guide.py` state machine and `GET /api/guide`, the mascot and its bubble (`static/guide.js`), "Ask me" through the existing chat with a reader level, and browser-only speech out and in.
  - Offline eval not regenerated: no scan, finding or claim logic changed.
- **2026-11-02 (MAYA pet):** Real character, emotions, roaming, working speech.
  - The placeholder mascot is replaced by MAYA herself, drawn in layered SVG in code (`static/pet.js`): 23 expressions and 8 poses, and a living pet that walks to the element she is talking about without covering other controls, can be dragged, and has idle behaviour.
  - Emotions come from the guide state machine and real page events.
  - Speech: a "Turn on voice" gesture, voice loading with retries and a status line, local voices first, sentence queueing, and lip-sync from boundary events with a timed fallback.
  - The shield "M" emblem is now the logo mark and favicon, and MAYA stands on the sign-in hero.
  - Offline eval not regenerated: no scan, finding or claim logic changed.
- **2026-11-02 (Pet v2, tier 1):** Compact pet, settings page, female voice, Hinglish rewrite.
  - The pet is about 80 px and speaks through a small bubble with chips, placed so it never covers the control it is talking about. She has only mute and hide on her; everything else moved to a Pet settings page.
  - Voice: a picker with Preview, a female-sounding default (never a male-sounding one), pitch 1.2 and the user's choice remembered.
  - Lines: every line rewritten as MAYA in the first person, with Devanagari spoken versions for a hi-IN voice. A first-visit greeting asks for your level.
  - The main chat now sends the chosen level, so answers are worded for it.
- **2026-11-02 (Pet v2, tier 2):** Hover guide, learning, work/play modes, post-scan tour.
  - Hover guide: a fixed registry in `static/pet-brain.js` says what each control is, what a click does and its effect. The pet glides beside the control and never covers it.
  - Learning: per-device use counts that shorten explanations and drive one idle suggestion.
  - Work/play: a work/play state machine with rate-limited antics.
  - Tour: after a scan the pet walks through the real findings in severity order. The pet still only points and explains.
- **2026-11-02 (Pet v2, tier 3):** The pet can help with safe actions.
  - "Let the pet help me", off by default, is turned on through a consent card. An allowlist in `static/pet-brain.js` permits only scroll, open or close a finding card, switch the view or summary language, highlight, and open a read-only panel. A never-list covers every state-changing control.
  - Every action and refusal is logged in Pet settings; Esc or a manual click cancels anything pending.
  - The tests try every forbidden control with every action and expect a refusal.
- **2026-11-02 (Pet v2 fixes):** Clickable chips, speech from the first gesture, hover guide everywhere, idle life.
  - The level chips now visibly continue the guide (happy face, next line) instead of asking a second question.
  - Speech is on by default and unlocks on the first gesture; mute is the only silence switch.
  - Hover explanations run and are spoken on every page, including sign-in.
  - Idle life: one 1-second scheduler with explicit states. A pet left beside a control now comes home, and the end of WORK resets the idle wait.
  - `scripts/pet_check.py` checks all of this with real mouse and key events in headless Chrome.
- **2026-11-02 (Pet v3):** Complete explanations, audience-aware English lines, real hover tests.
  - English only for now; the language switch is hidden. On the first visit the pet also asks what you run (shop, company, clinic, developer, something else); Pet settings can change it.
  - Every interactive control on every screen has a fixed entry (`scripts/pet_registry_check.py`: 59 screens, 0 controls without one). The first hover says what a click does and where the data goes; later hovers say one sentence.
  - Guide steps, tour, approval and re-test lines exist for every level and audience. Clinic lines give no medical advice and make no compliance claims.
  - `scripts/pet_review.py` walked five audience x level combinations with real mouse events (shop/new, clinic/new, developer/technical, company/new, other/new; 655 lines) and checked 26 facts against the running app, with no mismatch. After reading them, 29 sentences were rewritten and the tour got audience wording for every finding; 14 hover lines were rewritten earlier. The other five combinations were not walked in a browser, because RAM was short; their tour and onboarding lines are listed in `docs/pet_lines_review.md`, generated from the API.
  - One wrong data line fixed: Disconnect said it deletes your data; it removes the site, its reminder and its verified status, and past scans stay until "Delete my data".
  - The Chrome-driven scripts now stop the whole Chrome process tree; before, its child processes stayed alive after each run.
- **2026-11-02 (Visual v2: result page layout):** New tokens and shell, and a reorganised result page. Nothing was added or removed.
  - Tokens from the MAYA Visual Design System: palette, severity colours with dark text, 8 px spacing, 16-20 px radius, type scale, glass sidebar and form card, emerald primary buttons, danger and disabled states. The design's muted gray failed contrast on cards (4.0:1), so muted text is a lighter gray (5.4:1).
  - Result page: score, Fix first and findings come first. The live trace is on top only while a scan runs, then collapses to one line (steps, tokens, status); your choice is remembered.
  - One sticky action bar: Re-scan is the only primary button, the downloads are in an Export menu, and Reset demo app and Disconnect are in a More menu with danger styling (same handlers).
  - Compact trace rows with severity-coloured result chips; zero-finding calls fold.
  - Token tiles and a one-line-per-entry audit table in a scroll box. Room for the composer at the bottom. Two columns of findings on wide screens.
  - `scripts/result_page_check.py` walks it with real mouse and keyboard events: menus, trace collapse, approve all, apply, re-test, and the pet tour (score, 12 findings in severity order, Fix first, Approve).
- **2026-11-03 (Brand):** The original MAYA logo artwork, with blinking eyes.
  - The logo is the original artwork: a byte-identical static PNG (same-origin) plus lossless 1024, 512 and 64 px copies, with a blinking eyelid overlay and an alpha filter on top. The filter makes the art's black background transparent. Measured on black with the eyes open, the mean difference from the original is 1.833 out of 255 with the filter (0.55 with the earlier screen blend).
  - The eyes blink through two eyelids measured on the artwork (centres, angles, glow size and the face colour around the eyes), so the glow disappears too. One scheduler runs for all logos: every 3-6 s at random, sometimes twice, paused in a hidden tab. The blink is disabled when the OS "reduce motion" setting is on (on Windows: Animation effects off), and the eyes stay open.
  - The logo appears in the sidebar (44 px), sign-in (48 px), the chat home (112 px), the cards (64 px), the result header (36 px), the favicon (64 px PNG) and the printable report. A soft glow pulses only while a real scan or re-test runs.
- **2026-11-03 (Submission hygiene):** README status table, live-run figures, data-flow table and limits; Calm mode; one visible mascot on sign-in.
  - Calm mode and the sign-in mascot are checked with real mouse events by `scripts/pet_calm_check.py`.
  - Stale docs fixed: the pet is English only since Pet v3; the old indigo accent and `favicon.svg` entries are marked as historical.

## License

MIT. See [LICENSE](LICENSE).
