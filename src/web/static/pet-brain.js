"use strict";
// MAYA pet brain (tier 2): what each control does (fixed text), how much to explain given how often the user
// has used it, work vs play mode, rate-limited antics, and the post-scan tour stops. Pure data and functions,
// no DOM access, unit-tested in Node. Nothing here is from a model, and nothing is sent anywhere: the pet's
// memory of used controls stays in this browser's localStorage.
// English only for now; audience words come from AUD ({site}, {people}).
(function () {
  // ---------- what each control does (English, fixed text) ----------
  // Effect of a click. "sends" effects name where the data goes; destinations match the code (see docs/design.md).
  const EFFECTS = {
    reads: { label: "only reads", say: "It only reads; nothing is changed." },
    view: { label: "just changes what you see", say: "Nothing is sent or changed." },
    nowhere: { label: "nothing leaves this computer", say: "Nothing leaves this computer." },
    saves: { label: "saves to your account on this computer", say: "It saves to your account on this computer." },
    demo: { label: "changes your demo app", say: "It changes your practice demo app, and nothing else." },
    approval: { label: "needs your approval", say: "It records your approval; nothing changes yet." },
    deletes: { label: "deletes your data", say: "It deletes your data." },
    session: { label: "signs you out", say: "It signs you out." },
    sendsSite: { label: "sends read-only requests to {site}", say: "It sends read-only requests to {site}." },
    scanSite: { label: "sends read-only requests to {site}",
      say: "With a model key set, the masked results also go to the AI model provider." },
    device: { label: "remembered in this browser only", say: "It's remembered in this browser only." },
    field: { label: "a field to fill in", say: "Nothing is sent until you press the button." },
    sendsModel: { label: "sends data to the AI model provider", say: "Your question and your verified findings go to the AI model provider." },
    sendsLocal: { label: "stays on this computer", say: "The file goes to MAYA on this computer and is never saved." },
    sendsGithub: { label: "sends data to GitHub", say: "The repository name goes to GitHub." },
    sendsDns: { label: "sends domain names to cloudflare-dns.com and crt.sh",
      say: "Domain names go to cloudflare-dns.com and crt.sh; the lookalike sites are never visited." },
    sendsDoh: { label: "sends your domain name to cloudflare-dns.com", say: "Only your domain name goes to cloudflare-dns.com." },
  };
  // audience words used in the lines ({site}, {people})
  const AUD = {
    shop: { site: "your shop's website", people: "customers" },
    company: { site: "your company website", people: "staff and clients" },
    clinic: { site: "your clinic's booking site", people: "patients" },
    developer: { site: "your app", people: "users" },
    other: { site: "your website", people: "visitors" },
  };
  // key -> n: name, d: what a click does, e: effect, t: technical wording (optional), a: what happens after a click
  const G = {
    // sign-in and onboarding
    "tab-signin": { n: "Sign in", d: "shows the sign-in form.", e: "view" },
    "tab-signup": { n: "Create account", d: "shows the form for a new account.", e: "view" },
    "signin-email": { n: "Email", d: "your account name here; it stays on this computer.", e: "field" },
    "signin-password": { n: "Password", d: "checked here and stored only as a scrypt hash.", e: "field",
      t: "is verified against a salted scrypt hash; repeated failures are rate limited." },
    "signup-email": { n: "Email", d: "becomes your account name; it stays on this computer.", e: "field" },
    "signup-password": { n: "Password", d: "at least ten characters, stored only as a scrypt hash.", e: "field" },
    "signin-submit": { n: "Sign in", d: "opens your account.", e: "nowhere" },
    "signup-submit": { n: "Create account", d: "makes your account on this computer.", e: "saves", x: "Your password is stored only as a scrypt hash." },
    "theme-toggle": { n: "Theme", d: "switches between dark and light.", e: "view" },
    "choose-individual": { n: "Individual", d: "for learning and asking questions.", e: "saves" },
    "choose-company": { n: "Company", d: "for checking a website you own.", e: "saves" },
    "asset-website": { n: "Website", d: "picks a website as the thing to check.", e: "saves" },
    "connect-yes": { n: "Yes, continue", d: "opens the next step; nothing is scanned yet.", e: "saves" },
    "connect-no": { n: "Not now", d: "stops here; nothing is connected.", e: "saves" },
    "consent-allow": { n: "Allow", d: "records your consent to read-only checks.", e: "approval", x: "Nothing is scanned until you press Run security scan." },
    "consent-back": { n: "Back", d: "goes back a step without agreeing to anything.", e: "view" },
    "signout": { n: "Sign out", d: "ends your session on this computer.", e: "session", x: "Your sites and results stay in your account." },
    "brand-home": { n: "MAYA", d: "takes you to the home screen.", e: "view" },
    // connect a website
    "asset-url": { n: "Website address", d: "type the address of a site you own here.", e: "field" },
    "asset-add": { n: "Add website", d: "adds the site and gives you a token to prove it's yours.", e: "saves" },
    "use-demo": { n: "Use the demo app", d: "adds my practice shop, which runs only on this computer.", e: "saves",
      a: "This is my practice shop. It runs only on this computer, so you can try everything without touching a real site." },
    "verify-btn": { n: "Verify ownership", d: "looks for your token on the site.", e: "sendsSite",
      t: "fetches the page or /.well-known/maya-verify.txt and compares the token." },
    "verify-dns-btn": { n: "Verify via DNS", d: "looks up your TXT record.", e: "sendsDoh" },
    "ap-continue": { n: "Continue", d: "takes you to the main screen.", e: "view" },
    // sidebar
    "sidebar-open": { n: "Menu", d: "opens the sidebar.", e: "view" },
    "sidebar-close": { n: "Close", d: "closes the sidebar.", e: "view" },
    "new-chat": { n: "New chat", d: "starts an empty chat.", e: "saves", a: "A fresh chat. Ask me anything about web security or your own results." },
    "chats-empty-btn": { n: "Start a chat", d: "starts your first chat.", e: "saves" },
    "chat-item": { n: "Chat", f: "This is your chat \u201c{name}\u201d. Click to open it again.", s: "Your chat \u201c{name}\u201d.", e: "view" },
    "asset-item": { n: "Connected item", f: "This is {name}. Click to see its latest results.", s: "{name}: open its results.", e: "view" },
    "add-asset": { n: "Connect a website", d: "opens the page where you add a site you own.", e: "view",
      a: "This is where you connect a website you own. First I'll ask you to prove it's yours, then I run read-only checks." },
    "conn-website": { n: "Website", d: "connects a site you own for read-only checks.", e: "view",
      a: "This is where you connect a website you own. First I'll ask you to prove it's yours, then I run read-only checks." },
    "conn-code": { n: "Source code ZIP", d: "lets you upload your code for checks.", e: "sendsLocal",
      a: "Pick a ZIP of your code. I read it in memory, never run it and never save it." },
    "conn-logs": { n: "Logs", d: "lets you upload a server log to check.", e: "sendsLocal",
      a: "Pick a server log. I look for repeated failed logins and odd requests, then forget the file." },
    "conn-msg": { n: "Suspicious message", d: "checks a screenshot of a message.", e: "sendsModel",
      a: "Upload a screenshot. It goes to the AI model only after you tick the box.", x: "The screenshot goes to the AI model provider only after you tick the box." },
    "conn-github": { n: "GitHub repo", d: "fetches a public repository to check.", e: "sendsGithub",
      a: "Paste a public GitHub address. I download the ZIP, read it in memory and never run it." },
    "conn-watch": { n: "Lookalike watch", d: "looks for names that imitate your domain.", e: "sendsDns",
      a: "Type your own domain. I only ask DNS and certificate logs about lookalikes; I never visit them." },
    "pet-call": { n: "Call pet", d: "brings me back to my corner.", e: "view" },
    "pet-settings-btn": { n: "Pet settings", d: "opens my settings for level, audience and voice.", e: "view",
      a: "Here you can tell me how much detail you like and what you run, and pick my voice." },
    "data-link": { n: "Your data", d: "shows what MAYA stores and lets you delete it.", e: "view",
      a: "This page lists everything MAYA keeps about you, and the button to delete it." },
    "guide-launcher": { n: "Call MAYA", d: "brings me back.", e: "view" },
    // chat
    "suggest-scan": { n: "Check my website's security", d: "asks me in the chat how a scan of {site} works.", e: "sendsModel",
      t: "sends this question with your verified findings to the model; it does not start a scan." },
    "suggest-explain": { n: "Explain a vulnerability simply", d: "asks me in the chat to explain one issue in plain words.", e: "sendsModel" },
    "suggest-fix-first": { n: "What should I fix first?", d: "asks me to rank your own findings.", e: "sendsModel",
      t: "sends the question plus your verified findings; the answer is labelled unverified model text." },
    "suggest-how": { n: "How MAYA works", d: "asks what I do and never do when I check a site.", e: "sendsModel" },
    "prompt": { n: "Message box", d: "type your question here.", e: "field" },
    "send": { n: "Send", d: "sends your message.", e: "sendsModel",
      t: "sends the message and your verified findings to the model provider; replies carry a label." },
    "investigate-btn": { n: "Investigate", d: "lets the AI look through your own results with read-only tools.", e: "sendsModel", x: "Your question and your results go to the AI model provider." },
    "tool-calls": { n: "Tool calls", d: "shows what the investigation looked at.", e: "view" },
    // results
    "scan-btn": { n: "Run security scan", d: "checks {site} like a visitor would, changing nothing.", e: "scanSite",
      t: "runs scope-guarded GET/HEAD checks; real sites also get a DNS lookup at cloudflare-dns.com and one TLS handshake." },
    "scan-empty-btn": { n: "Run your first scan", d: "checks {site} like a visitor would, changing nothing.", e: "scanSite" },
    "reset-demo-btn": { n: "Reset demo app", d: "puts the practice shop back in its broken state.", e: "demo" },
    "disconnect-btn": { n: "Disconnect", d: "removes this site, its reminder and its verified status from MAYA.", e: "deletes",
      s: "Disconnect: removes this site from MAYA.", x: "Past scan results stay until you use Delete my data." },
    "summarize-btn": { n: "Summarize with model", d: "asks the AI for a short summary of these results.", e: "sendsModel", x: "These results go to the AI model provider." },
    "score-card": { n: "Score ring", d: "a rough grade for the checks that ran.", e: "reads",
      t: "is a heuristic A to F score from the fixed severity table, only over the checks that ran." },
    "trace-step": { n: "Trace step", f: "{name} of the live trace: what I checked and what came back. It only shows a step; nothing is changed.", s: "{name} of the trace.", e: "reads" },
    "finding": { n: "Finding", f: "This is the finding for {name}. Open it to see the evidence and the fix.", s: "Finding: {name}.", e: "view" },
    "fixfirst-card": { n: "Fix first", f: "{name} is one of the three things to fix first. Open it to see why it ranks here.", s: "Fix first: {name}.", e: "reads" },
    "role-owner": { n: "Owner view", d: "explains findings in plain words.", e: "view", a: "Plain words now, with an everyday example for each finding." },
    "role-developer": { n: "Developer view", d: "shows the technical detail and the fix.", e: "view", a: "Now each finding shows the technical detail and the exact fix." },
    "role-technical": { n: "Technical view", d: "shows check ids, finding ids and severity.", e: "view", a: "Technical view: check ids, finding ids and the severity table." },
    "copy-issue": { n: "Copy as GitHub issue", d: "copies this finding as issue text.", e: "nowhere" },
    "proposal-card": { n: "Fix proposal", f: "The fix proposal for {name}: a fixed template with its risk and a way to roll it back. Nothing changes until you approve and apply it.", s: "Fix proposal for {name}.", e: "reads" },
    "copy-snippet": { n: "Copy", d: "copies this snippet to your clipboard.", e: "nowhere" },
    "approver": { n: "Approver name", d: "your name goes into the audit log with each approval.", e: "approval", x: "The log stays on this computer." },
    "proposal-approve": { n: "Approve", d: "records your approval for this fix; nothing changes until you press Apply.", e: "approval", x: "Only the audit log on this computer is written." },
    "approve-all-btn": { n: "Approve all", d: "records your approval for every open fix; nothing changes until you press Apply.", e: "approval", x: "Only the audit log on this computer is written.",
      t: "writes one approval per proposal to the hash-chained audit log; it applies nothing." },
    "apply-btn": { n: "Apply approved fixes", d: "applies only the fixes you approved, to the practice demo app, then checks it still works.", e: "demo",
      t: "edits only the loopback demo app's control file, runs a health check and rolls back on failure." },
    "retest-btn": { n: "Re-test", d: "runs the same checks again to see what really changed.", e: "sendsSite" },
    "sum-en": { n: "English summary", d: "shows the owner summary in English.", e: "view" },
    "sum-hinglish": { n: "Hinglish summary", d: "shows the owner summary in Hinglish.", e: "view" },
    "reword-btn": { n: "Reword for me", d: "asks the AI to rephrase the summary; any new numbers are dropped.", e: "sendsModel", x: "The summary goes to the AI model provider." },
    "reminder-days": { n: "Reminder", d: "when to remind you to re-scan.", e: "field" },
    "reminder-btn": { n: "Set reminder", d: "saves a re-scan reminder. Nothing runs by itself.", e: "saves" },
    "reminder-ics": { n: "Add to calendar", d: "downloads a calendar file.", e: "nowhere" },
    "dl-pack": { n: "Evidence pack", d: "downloads the stored evidence with its hashes.", e: "nowhere" },
    "dl-report": { n: "Download report", d: "downloads the full report.", e: "nowhere" },
    "dl-report-md": { n: "Report as Markdown", d: "downloads the report as a text file.", e: "nowhere" },
    "dl-issues": { n: "issues.md", d: "downloads every finding as issue text.", e: "nowhere" },
    "dl-client": { n: "Client report", d: "downloads one page you can share by hand.", e: "nowhere" },
    "dl-print": { n: "Printable report", d: "opens a page to print or save as PDF.", e: "nowhere" },
    "export-menu": { n: "Export", d: "opens the downloads: evidence pack, reports and issue text.", e: "view" },
    "more-menu": { n: "More", d: "opens Reset demo app and Disconnect.", e: "view", x: "Opening it changes nothing; each button inside still explains itself." },
    "trace-toggle": { n: "Live trace", d: "shows or hides every step of the scan.", e: "view" },
    "trace-zero": { n: "Checks with no findings", f: "{name}: checks that found nothing, folded into one line. Open it to see each one.", s: "{name}: checks with 0 findings.", e: "view" },
    "audit-details": { n: "Audit entry", f: "The details of audit {name}. Click to show the whole line.", s: "Details of audit {name}.", e: "view" },
    "audit-hash": { n: "Hash", f: "The hash of audit {name}. Click to copy the full hash.", s: "Copy the hash of {name}.", e: "nowhere" },
    // uploads, GitHub, watch, message
    "up-file": { n: "Choose file", d: "pick the file to check.", e: "field" },
    "up-btn": { n: "Upload and check", d: "checks the chosen file.", e: "sendsLocal" },
    "msg-confirm": { n: "Send to the model", d: "your OK to send the screenshot to the AI.", e: "sendsModel", x: "Only then does the screenshot go to the AI model provider." },
    "msg-include": { n: "Include the image", d: "keeps the image in the evidence pack, in memory only.", e: "nowhere" },
    "official-box": { n: "Official domains and numbers", d: "opens your official lists.", e: "view" },
    "official-domains": { n: "Official domains", d: "the domains that really belong to you.", e: "field" },
    "official-numbers": { n: "Official phone numbers", d: "your real phone numbers.", e: "field" },
    "official-save": { n: "Save lists", d: "saves your official domains and numbers.", e: "saves" },
    "check-dns": { n: "Check DNS", d: "asks whether {name} exists, without visiting it.", e: "sendsDoh" },
    "note-domain": { n: "Domain to report", d: "which domain the draft is about.", e: "field" },
    "note-registrar": { n: "Draft registrar note", d: "writes a draft report. MAYA never sends it.", e: "nowhere" },
    "note-platform": { n: "Draft platform note", d: "writes a draft report. MAYA never sends it.", e: "nowhere" },
    "note-approver": { n: "Approver name", d: "goes on a draft note when you approve it.", e: "approval", x: "It stays on this computer." },
    "note-approve": { n: "Approve note", d: "marks this draft READY.", e: "approval", x: "MAYA still never sends it; you copy it yourself." },
    "copy-note": { n: "Copy", d: "copies the draft note.", e: "nowhere" },
    "github-url": { n: "Repository address", d: "the public GitHub repository to check.", e: "field" },
    "github-btn": { n: "Fetch and check", d: "downloads the public repository and checks it in memory.", e: "sendsGithub" },
    "watch-domain": { n: "Your domain", d: "the domain to watch for lookalikes.", e: "field" },
    "watch-btn": { n: "Start the watch", d: "looks up lookalike names once.", e: "sendsDns" },
    // your data
    "delete-confirm": { n: "Confirm delete", d: "type DELETE here to confirm.", e: "field" },
    "delete-btn": { n: "Delete my data", d: "removes your sites, scans, evidence and chats. Your account stays.", e: "deletes" },
    // pet settings
    "pet-level": { n: "Experience level", d: "sets how much detail I give.", e: "device" },
    "pet-audience": { n: "What you run", d: "changes my examples to fit you.", e: "device" },
    "pet-lang": { n: "Language", d: "English for now.", e: "device" },
    "pet-voice": { n: "Voice", d: "picks which installed voice I use.", e: "device", x: "Online voices may send the spoken text to their provider." },
    "pet-voice-preview": { n: "Preview", d: "plays a short sample of that voice.", e: "nowhere", x: "It uses your browser's voice; an online voice may send the sentence to its provider." },
    "pet-rate": { n: "Speed", d: "sets how fast I talk.", e: "device" },
    "pet-pitch": { n: "Pitch", d: "sets how high my voice is.", e: "device" },
    "pet-speak-steps": { n: "Speak guide lines", d: "turns my spoken guide lines on or off.", e: "device" },
    "pet-speak-hover": { n: "Speak hover explanations", d: "turns spoken hover lines on or off.", e: "device" },
    "pet-calm": { n: "Calm mode", d: "keeps me in my corner with no idle antics; my bubble and speech stay.", e: "device" },
    "pet-speak-answers": { n: "Speak answers", d: "reads a one-line summary of chat answers.", e: "device" },
    "pet-voice-test": { n: "Test voice", d: "says one short sentence.", e: "nowhere", x: "It uses your browser's voice; an online voice may send the sentence to its provider." },
    "pet-voice-stop": { n: "Stop", d: "stops talking.", e: "nowhere", x: "Nothing else changes." },
    "pet-mic": { n: "Microphone", d: "lets you speak your question; your browser turns it into text.", e: "device" },
    "pet-help": { n: "Let the pet help me", d: "lets me scroll and open read-only panels for you.", e: "device" },
    "pet-help-allow": { n: "Turn on", d: "turns help on after you've read the list.", e: "device" },
    "pet-help-cancel": { n: "Cancel", d: "leaves help off.", e: "view" },
    "pet-reset": { n: "Reset what I learned", d: "forgets which buttons you've used on this device.", s: "Reset what I learned: clears this browser's memory of me.", e: "device", x: "Only this browser's memory is cleared." },
  };
  const DYNAMIC = { en: "This is {name}. Open it to see the details." };
  // the first hover says what a click does and then where the data goes; "signs you out" adds nothing to "Sign out"
  const SAY_IN_FULL = Object.keys(EFFECTS).filter((k) => k !== "session");
  // controls worth suggesting when the user is idle and has never tried them, most useful first
  const SUGGEST = ["fixfirst-panel", "role-developer", "dl-pack", "dl-client", "reminder-btn", "dl-issues"];
  const SUGGEST_NAMES = { "fixfirst-panel": "Fix first" };

  function fill(s, aud, name, site) {
    const a = AUD[aud] || AUD.other;
    return String(s).replace(/\{site\}/g, site || a.site).replace(/\{people\}/g, a.people).replace(/\{name\}/g, name || "this item");
  }
  // guide key: data-guide first, then the element id (classes are never used, so one key is one kind of control)
  function keyFor(guideKey, id) {
    if (guideKey && G[guideKey]) return guideKey;
    if (id && G[id]) return id;
    return null;
  }
  // the full explanation the first time, one sentence once the control has been used
  function detailFor(count) { return count ? "short" : "full"; }
  // o: {count, name (clean accessible name for dynamic items), audience, level}; returns {text, spoken, isNew, known}
  function hoverText(key, o) {
    const opt = o || {};
    const e = G[key];
    if (!e) {  // only dynamic content without a registry entry: a sentence from its clean accessible name
      const text = opt.name ? fill(DYNAMIC.en, opt.audience, opt.name) : "This is part of MAYA. Hover the buttons around it and I'll explain each one.";
      return { text: text, spoken: text, isNew: false, known: false };
    }
    const site = opt.demo ? "the practice demo app" : null;
    const name = fill(e.n, opt.audience, opt.name, site);
    const eff = EFFECTS[e.e];
    const does = fill(opt.level === "technical" && e.t ? e.t : e.d, opt.audience, opt.name, site);
    if (detailFor(opt.count || 0) === "short") {
      const short = e.s ? fill(e.s, opt.audience, opt.name, site)
        : name + (/[?!]$/.test(name) ? " It " : ": ") + fill(eff.label, opt.audience, null, site) + ".";
      return { text: short, spoken: short, isNew: false, known: true };
    }
    // "Name: what a click does. Where the data goes." (dynamic items use their own sentence)
    const sep = /[?!]$/.test(name) ? " It " : ": ";  // "What should I fix first? It asks ..." reads better than "?:"
    const extra = e.x !== undefined ? e.x : SAY_IN_FULL.indexOf(e.e) >= 0 ? fill(eff.say, opt.audience, null, site) : "";
    const full = e.f ? fill(e.f, opt.audience, opt.name, site) : (name + sep + does + (extra ? " " + extra : "")).trim();
    return { text: full, spoken: full, isNew: true, known: true };
  }
  // what to say right after a click on this control (null if the page itself shows what happened)
  function afterText(key, audience) {
    const e = G[key];
    return e && e.a ? fill(e.a, audience) : null;
  }
  function bump(used, key) {
    const out = Object.assign({}, used || {});
    out[key] = Math.min(999, (out[key] || 0) + 1);
    return out;
  }
  // the first suggestion candidate that is visible and never used
  function pickSuggestion(visible, used) {
    for (const k of SUGGEST) if (visible.indexOf(k) >= 0 && !((used || {})[k])) return k;
    return null;
  }
  function suggestText(key) {
    return "You haven't tried this yet: " + (SUGGEST_NAMES[key] || (G[key] ? G[key].n : key)) + ".";
  }

  // WORK while something needs attention: a scan or re-test running, an approval pending, typing, or the pointer
  // on a control in the last 6 s. Idle timing is the scheduler's job (idleTick), not the mode's.
  const WORK_STATES = ["scan_running", "proposals_not_approved", "approved_not_applied", "applied_not_retested"];
  function mode(s) {
    if (s.scanRunning || s.typing || WORK_STATES.indexOf(s.state) >= 0) return "work";
    if (s.sinceHoverMs < 6000) return "work";
    return "play";
  }
  const ANTICS = ["wink", "dance", "look", "peek", "hide", "chase"];
  const ANTIC_GAP_MS = 25000;
  function nextAntic(s) {
    if (s.mode !== "play" || s.reduced || s.now - (s.lastAnticAt || 0) < ANTIC_GAP_MS) return null;
    return ANTICS[(s.count || 0) % ANTICS.length];
  }

  // ---------- idle life: one scheduler, ticked every second ----------
  // phases: ACTIVE (input in the last 8 s) -> IDLE_WAIT (first antic 8-15 s into the idle time: look around or
  // wave) -> PLAYING (one antic every 15-25 s) -> SLEEPY (45 s idle). The first input after SLEEPY is a "wake"
  // (surprised). WORK pauses antics. Reduced motion keeps expression-only actions (wink, sleepy, wake).
  const IDLE_ANTICS = ["wink", "wave", "dance", "lookPointer", "peek", "stretch"];
  const QUIET_ANTICS = ["wink"];
  function rand(seed, lo, hi) {  // deterministic: same seed, same delays (for tests and for calm behaviour)
    const x = Math.sin(seed * 9301 + 49297) * 233280;
    return lo + Math.floor((x - Math.floor(x)) * (hi - lo + 1));
  }
  function idleStart() { return { phase: "ACTIVE", nextAt: 0, count: 0 }; }
  function idleTick(st, inp) {
    const s = Object.assign({}, st);
    if (inp.work) { s.workUntil = inp.now; return [s, null]; }  // the end of WORK counts like fresh input
    const since = Math.max(inp.lastInputAt, s.workUntil || -Infinity);
    const idle = inp.now - since;
    if (idle < 1000) {  // fresh input
      const woke = s.phase === "SLEEPY";
      return [{ phase: "ACTIVE", nextAt: 0, count: s.count, workUntil: s.workUntil }, woke ? "wake" : null];
    }
    if (idle >= 45000 && s.phase !== "SLEEPY") return [Object.assign(s, { phase: "SLEEPY" }), "sleepy"];
    if (s.phase === "SLEEPY") return [s, null];
    if (s.phase === "ACTIVE" && idle >= 8000) {
      s.phase = "IDLE_WAIT";
      s.nextAt = since + rand(s.count + 1, 8, 15) * 1000;
    }
    if ((s.phase === "IDLE_WAIT" || s.phase === "PLAYING") && inp.now >= s.nextAt) {
      const first = s.phase === "IDLE_WAIT";
      const pool = inp.reduced ? QUIET_ANTICS : (first ? ["lookPointer", "wave"] : IDLE_ANTICS);
      const name = pool[s.count % pool.length];
      s.count += 1;
      s.phase = "PLAYING";
      s.nextAt = inp.now + rand(s.count + 7, 15, 25) * 1000;
      return [s, name];
    }
    return [s, null];
  }

  // post-scan tour: score, then each finding in the code's severity order, then Fix first, then the approve step.
  // input = {findings: [{target, severity, id}], lines: {score, findings: {id: line}, fixfirst, approve}} where the
  // lines come from GET /api/guide (audience x level, built from stored results). Only numbers from the inputs appear.
  const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  function tourStops(input) {
    const lines = input.lines || {};
    const notes = lines.findings || {};
    const findings = (input.findings || []).slice().sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
    const stops = [];
    if (!findings.length) {
      stops.push({ target: "score-card", expression: "proud",
        text: lines.score || "Here's your score: no issues detected in the checks run. That covers only these checks." });
      return stops;
    }
    const worst = findings[0].severity;
    stops.push({ target: "score-card", expression: worst === "critical" || worst === "high" ? "nervous" : "curious",
      text: lines.score || "This is your score: " + input.scoreLine + ". The checks found " + findings.length + " issue(s)." });
    findings.forEach((f, i) => {
      const fallback = f.severity.charAt(0).toUpperCase() + f.severity.slice(1) + " issue, " + (i + 1) + " of " +
        findings.length + ": " + f.title + " at " + f.location + ".";
      stops.push({ target: f.target, severity: f.severity, expression: SEV_RANK[f.severity] <= 1 ? "alert" : "curious",
        text: notes[f.id] || fallback });
    });
    stops.push({ target: "fixfirst-panel", expression: "determined",
      text: lines.fixfirst || "These Fix first cards are the three things to do first." });
    stops.push({ target: "proposals-panel", expression: "determined",
      text: lines.approve || "Here are the fix proposals. Approving is your decision; I never approve for you." });
    return stops;
  }

  // ---------- tier 3: "Let the pet help me" (off by default; the user turns it on after reading the list) ----------
  // The pet may ONLY do these things itself. Nothing on this list changes data or sends anything.
  const HELP_ACTIONS = ["scroll", "expand", "collapse", "role", "summary-lang", "highlight", "open-panel"];
  // Controls the pet never presses, whatever the action: anything that changes state, sends data, uploads,
  // approves, applies, deletes, signs out, or needs typed input. For these it only shows the user where they are.
  const NEVER_IDS = ["approve-all-btn", "apply-btn", "retest-btn", "scan-btn", "scan-empty-btn", "reset-demo-btn",
    "consent-allow", "connect-yes", "connect-no", "verify-btn", "verify-dns-btn", "delete-btn", "delete-confirm",
    "disconnect-btn", "signout", "up-btn", "up-file", "official-save", "official-domains", "official-numbers",
    "reminder-btn", "reminder-days", "approver", "note-approver", "note-registrar", "note-platform", "github-btn",
    "github-url", "watch-btn", "watch-domain", "summarize-btn", "reword-btn", "investigate-btn", "new-chat", "use-demo",
    "asset-url", "asset-form", "choose-company", "choose-individual", "asset-website", "msg-confirm", "msg-include",
    "prompt", "composer", "pet-help", "pet-reset", "data-link", "conn-code", "conn-logs", "conn-msg", "conn-github",
    "conn-watch", "send", "suggest-scan", "suggest-explain", "suggest-fix-first", "suggest-how", "proposal-approve",
    "note-approve", "check-dns", "asset-add", "signin-submit", "signup-submit", "chats-empty-btn", "pet-level",
    "pet-audience", "pet-lang", "pet-voice", "pet-rate", "pet-pitch", "pet-speak-steps", "pet-speak-hover",
    "pet-speak-answers", "pet-calm", "pet-mic", "pet-help-allow", "official-box"];
  const NEVER_CLASSES = ["send", "copy-btn", "copy-issue", "suggestion", "role-btn", "pet-chip", "btn-danger"];
  const ROLES = ["owner", "developer", "technical"];
  // a: {helpOn, action, id, classes, tag, value, isFinding, isReadOnlyDetails}
  function checkAction(a) {
    const refuse = (reason) => ({ ok: false, reason: reason });
    if (!a || !a.helpOn) return refuse("help is off");
    if (HELP_ACTIONS.indexOf(a.action) < 0) return refuse("not an allowed action");
    if (a.id && NEVER_IDS.indexOf(a.id) >= 0) return refuse("MAYA never presses this for you");
    if ((a.classes || []).some((c) => NEVER_CLASSES.indexOf(c) >= 0)) return refuse("MAYA never presses this for you");
    if (a.tag === "FORM" || a.tag === "INPUT" || a.tag === "TEXTAREA" || a.tag === "SELECT") return refuse("MAYA never fills in or submits forms");
    if ((a.action === "expand" || a.action === "collapse") && !a.isFinding) return refuse("only finding cards open and close");
    if (a.action === "open-panel" && !a.isReadOnlyDetails) return refuse("only read-only panels open");
    if (a.action === "role" && ROLES.indexOf(a.value) < 0) return refuse("unknown view");
    if (a.action === "summary-lang" && ["en", "hinglish"].indexOf(a.value) < 0) return refuse("unknown language");
    if ((a.action === "scroll" || a.action === "highlight") && !a.id && !a.isFinding && !a.tag) return refuse("nothing to point at");
    return { ok: true, reason: null };
  }
  function logLine(ok, action, label, reason) {
    return ok ? "Pet did: " + action + " " + label : "Pet refused: " + action + " " + label + " (" + reason + ")";
  }
  // pending pet actions; Esc or any manual click cancels them all (timer functions are passed in for testing)
  function ActionQueue(setT, clearT) {
    const pending = new Set();
    return {
      add(fn, ms) { const id = setT(() => { pending.delete(id); fn(); }, ms || 0); pending.add(id); return id; },
      cancel() { const n = pending.size; pending.forEach((id) => clearT(id)); pending.clear(); return n; },
      size() { return pending.size; },
    };
  }

  const api = { EFFECTS, G, AUD, SUGGEST, keyFor, detailFor, hoverText, afterText, bump, pickSuggestion, suggestText,
    mode, nextAntic, ANTICS, ANTIC_GAP_MS, tourStops, WORK_STATES, HELP_ACTIONS, NEVER_IDS, NEVER_CLASSES,
    idleStart, idleTick, IDLE_ANTICS,
    checkAction, logLine, ActionQueue };
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  if (typeof window !== "undefined") window.MayaBrain = api;
})();
