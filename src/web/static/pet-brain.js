"use strict";
// MAYA pet brain (tier 2): what each control does (fixed text), how much to explain given how often the user
// has used it, work vs play mode, rate-limited antics, and the post-scan tour stops. Pure data and functions,
// no DOM access, unit-tested in Node. Nothing here is from a model, and nothing is sent anywhere: the pet's
// memory of used controls stays in this browser's localStorage.
// HINGLISH strings below NEED NATIVE-SPEAKER REVIEW before they are relied on.
(function () {
  // What a click does to the world. "sends" entries name the destination in their text.
  const EFFECTS = {
    reads: { en: "only reads", hinglish: "sirf padhta hai" },
    saves: { en: "saves to your MAYA account on this computer", hinglish: "is computer par aapke MAYA account mein save hota hai" },
    demo: { en: "changes something on your demo app", hinglish: "aapke demo app mein kuch badalta hai" },
    approval: { en: "needs your approval", hinglish: "aapki approval chahiye" },
    deletes: { en: "deletes your data", hinglish: "aapka data delete karta hai" },
    session: { en: "ends your session", hinglish: "aapka session band karta hai" },
    sendsSite: { en: "sends read-only requests to your own verified site (or the demo app)", hinglish: "aapki verified site (ya demo app) ko read-only requests bhejta hai" },
    sendsModel: { en: "sends data to the model provider", hinglish: "data model provider ko bhejta hai" },
    sendsLocal: { en: "sends the file to MAYA on this computer (read in memory, not saved)", hinglish: "file is computer par MAYA ko jaati hai (memory mein padhi jaati hai, save nahi hoti)" },
    sendsGithub: { en: "sends the repository name to GitHub (codeload.github.com)", hinglish: "repository ka naam GitHub (codeload.github.com) ko jaata hai" },
    sendsDns: { en: "sends domain names to cloudflare-dns.com and crt.sh", hinglish: "domain names cloudflare-dns.com aur crt.sh ko jaate hain" },
  };
  const H = (en, enClick, hi, hiClick, effect) => ({ en: [en, enClick], hinglish: [hi, hiClick], effect: effect });
  // id (or ".class") -> [what it is, what a click does] per language, and the effect
  const HOVER = {
    "pet-call": H("Call pet", "brings me back to my corner.", "Call pet", "mujhe wapas corner mein le aata hai.", "reads"),
    "pet-settings-btn": H("Pet settings", "opens my settings: level, language, voice.", "Pet settings", "meri settings kholta hai: level, language, voice.", "reads"),
    "new-chat": H("New chat", "starts an empty chat.", "New chat", "ek naya khaali chat shuru karta hai.", "saves"),
    "add-asset": H("Connect a website", "opens the page where you add your site.", "Connect a website", "wo page kholta hai jahan aap apni site add karte ho.", "reads"),
    "theme-toggle": H("Theme", "switches between dark and light.", "Theme", "dark aur light ke beech switch karta hai.", "reads"),
    "data-link": H("Your data", "shows what MAYA stores and lets you delete it.", "Your data", "dikhata hai MAYA kya store karti hai, aur delete ka option deta hai.", "reads"),
    "signout": H("Sign out", "signs you out of MAYA.", "Sign out", "aapko MAYA se sign out karta hai.", "session"),
    "conn-code": H("Source code ZIP", "lets you upload a ZIP of your code to check.", "Source code ZIP", "aapke code ki ZIP upload karke check karne deta hai.", "sendsLocal"),
    "conn-logs": H("Logs", "lets you upload a server log to check.", "Logs", "server log upload karke check karne deta hai.", "sendsLocal"),
    "conn-msg": H("Suspicious message", "lets you check a screenshot; it goes to the model provider only after you confirm.", "Suspicious message", "screenshot check karta hai; aapke confirm karne ke baad hi model provider ko jaata hai.", "sendsModel"),
    "conn-github": H("GitHub repo", "fetches a public repository to check.", "GitHub repo", "ek public repository check ke liye laata hai.", "sendsGithub"),
    "conn-watch": H("Lookalike watch", "looks up names that look like your domain.", "Lookalike watch", "aapke domain jaise dikhne wale naam dhoondhta hai.", "sendsDns"),
    "scan-btn": H("Run security scan", "runs my read-only checks; nothing on your site changes.", "Run security scan", "mere read-only checks chalata hai; aapki site par kuch nahi badalta.", "sendsSite"),
    "scan-empty-btn": H("Run your first scan", "runs my read-only checks; nothing on your site changes.", "Run your first scan", "mere read-only checks chalata hai; aapki site par kuch nahi badalta.", "sendsSite"),
    "reset-demo-btn": H("Reset demo app", "puts the practice shop back in its broken state.", "Reset demo app", "practice shop ko wapas purani broken state mein kar deta hai.", "demo"),
    "disconnect-btn": H("Disconnect", "removes this site from MAYA.", "Disconnect", "is site ko MAYA se hata deta hai.", "deletes"),
    "summarize-btn": H("Summarize with model", "asks the model for a short summary of these results.", "Summarize with model", "model se in results ki chhoti summary maangta hai.", "sendsModel"),
    "role-owner": H("Owner view", "explains findings in plain words.", "Owner view", "findings ko simple words mein samjhata hai.", "reads"),
    "role-developer": H("Developer view", "shows the technical detail and the fix.", "Developer view", "technical detail aur fix dikhata hai.", "reads"),
    "role-technical": H("Technical view", "shows check ids, finding ids and severity.", "Technical view", "check ids, finding ids aur severity dikhata hai.", "reads"),
    "approver": H("Approver name", "your name goes into the audit log with each approval.", "Approver name", "har approval ke saath aapka naam audit log mein jaata hai.", "approval"),
    "approve-all-btn": H("Approve all", "records your approval for every open fix; nothing is applied yet.", "Approve all", "har open fix par aapki approval record karta hai; abhi kuch apply nahi hota.", "approval"),
    "apply-btn": H("Apply approved fixes", "applies only the fixes you approved, on the demo app, with a health check.", "Apply approved fixes", "sirf approved fixes demo app par lagata hai, health check ke saath.", "demo"),
    "retest-btn": H("Re-test", "runs the same checks again to see what really changed.", "Re-test", "wahi checks dobara chalata hai, dekhne ke liye ki asal mein kya badla.", "sendsSite"),
    "dl-pack": H("Evidence pack", "downloads the stored evidence with hashes to your computer.", "Evidence pack", "stored evidence hashes ke saath aapke computer par download karta hai.", "reads"),
    "dl-report": H("Download report", "downloads the report to your computer.", "Download report", "report aapke computer par download karta hai.", "reads"),
    "dl-report-md": H("Report as Markdown", "downloads the report as a text file.", "Report as Markdown", "report text file ki tarah download karta hai.", "reads"),
    "dl-issues": H("issues.md", "downloads every finding as GitHub-issue text.", "issues.md", "har finding ko GitHub issue text ki tarah download karta hai.", "reads"),
    "dl-client": H("Client report", "downloads one page you can share by hand.", "Client report", "ek page download karta hai jo aap khud share kar sakte ho.", "reads"),
    "dl-print": H("Printable report", "opens a page you can print or save as PDF.", "Printable report", "ek page kholta hai jo print ya PDF save ho sakta hai.", "reads"),
    "reminder-btn": H("Set reminder", "saves a re-scan reminder; nothing runs by itself.", "Set reminder", "re-scan reminder save karta hai; kuch apne aap nahi chalta.", "saves"),
    "reminder-ics": H("Add to calendar", "downloads a calendar file.", "Add to calendar", "calendar file download karta hai.", "reads"),
    "sum-en": H("English summary", "shows the owner summary in English.", "English summary", "owner summary English mein dikhata hai.", "reads"),
    "sum-hinglish": H("Hinglish summary", "shows the owner summary in Hinglish.", "Hinglish summary", "owner summary Hinglish mein dikhata hai.", "reads"),
    "reword-btn": H("Reword for me", "asks the model to rephrase the summary; extra numbers get dropped.", "Reword for me", "model se summary dobara likhwata hai; naye numbers hata diye jaate hain.", "sendsModel"),
    "delete-btn": H("Delete my data", "deletes your sites, scans, evidence and chats.", "Delete my data", "aapki sites, scans, evidence aur chats delete karta hai.", "deletes"),
    "investigate-btn": H("Investigate", "lets the model look through your own results with read-only tools.", "Investigate", "model ko read-only tools se aapke results dekhne deta hai.", "sendsModel"),
    "up-btn": H("Upload and check", "checks the chosen file.", "Upload and check", "chuni hui file check karta hai.", "sendsLocal"),
    "github-btn": H("Fetch and check", "downloads the public repository and checks it.", "Fetch and check", "public repository download karke check karta hai.", "sendsGithub"),
    "watch-btn": H("Start the watch", "looks up lookalike names once.", "Start the watch", "lookalike naam ek baar dhoondhta hai.", "sendsDns"),
    "official-save": H("Save lists", "saves your official domains and numbers.", "Save lists", "aapke official domains aur numbers save karta hai.", "saves"),
    "note-registrar": H("Draft registrar note", "writes a draft; MAYA never sends it.", "Draft registrar note", "ek draft likhta hai; MAYA use kabhi nahi bhejti.", "reads"),
    "note-platform": H("Draft platform note", "writes a draft; MAYA never sends it.", "Draft platform note", "ek draft likhta hai; MAYA use kabhi nahi bhejti.", "reads"),
    "use-demo": H("Use the demo app", "adds my practice shop that runs only on this computer.", "Use the demo app", "meri practice shop add karta hai jo sirf is computer par chalti hai.", "saves"),
    "verify-btn": H("Verify ownership", "looks for your token on your site.", "Verify ownership", "aapki site par aapka token dhoondhta hai.", "sendsSite"),
    "verify-dns-btn": H("Verify via DNS", "asks cloudflare-dns.com for your TXT record.", "Verify via DNS", "cloudflare-dns.com se aapka TXT record poochta hai.", "sendsDns"),
    "connect-yes": H("Yes, continue", "shows the next step; nothing is scanned yet.", "Yes, continue", "agla step dikhata hai; abhi kuch scan nahi hota.", "saves"),
    "consent-allow": H("Allow", "records your consent to read-only checks.", "Allow", "read-only checks ke liye aapka consent record karta hai.", "approval"),
    "choose-company": H("Company", "for checking a website you own.", "Company", "apni website check karne ke liye.", "saves"),
    "choose-individual": H("Individual", "for learning and asking questions.", "Individual", "seekhne aur sawaal poochne ke liye.", "saves"),
    "asset-website": H("Website", "picks a website as the thing to check.", "Website", "check karne ke liye website chunta hai.", "saves"),
    ".finding-sum": H("A finding", "opens or closes its explanation, fix and evidence.", "Ek finding", "uski explanation, fix aur evidence kholta ya band karta hai.", "reads"),
    ".asset-item": H("A connected item", "opens its results.", "Ek connected item", "uske results kholta hai.", "reads"),
    ".suggestion": H("A suggested question", "sends this question to the model.", "Ek suggested sawaal", "ye sawaal model ko bhejta hai.", "sendsModel"),
    ".copy-btn": H("Copy", "copies the snippet to your clipboard.", "Copy", "snippet aapke clipboard mein copy karta hai.", "reads"),
    ".copy-issue": H("Copy as GitHub issue", "copies this finding as issue text.", "Copy as GitHub issue", "is finding ko issue text ki tarah copy karta hai.", "reads"),
    ".send": H("Send", "sends your message and your verified findings to the model provider.", "Send", "aapka message aur verified findings model provider ko bhejta hai.", "sendsModel"),
  };
  const GENERIC = { en: "This control is part of MAYA. Nothing happens until you click it.", hinglish: "Ye MAYA ka ek control hai. Click kiye bina kuch nahi hota." };
  const NEW_MARK = { en: "New: ", hinglish: "Naya: " };
  // controls worth suggesting when the user is idle and has never tried them, most useful first
  const SUGGEST = ["fixfirst-panel", "role-developer", "dl-pack", "dl-client", "reminder-btn", "sum-hinglish", "dl-issues"];
  const SUGGEST_LINE = { en: "You haven't tried this yet: ", hinglish: "Ye abhi tak try nahi kiya: " };
  const SUGGEST_NAMES = { "fixfirst-panel": { en: "Fix first", hinglish: "Fix first" } };

  // registry key for an element: its id if known, else the first known class
  function keyFor(id, classes) {
    if (id && HOVER[id]) return id;
    for (const c of classes || []) if (HOVER["." + c]) return "." + c;
    return null;
  }
  // full the first time, one line after a couple of uses, nothing for well-known controls
  function detailFor(count) {
    if (!count) return "full";
    return count < 5 ? "short" : "none";
  }
  function hoverText(key, lang, count) {
    const level = detailFor(count);
    if (level === "none") return null;
    const e = HOVER[key];
    if (!e) return { text: GENERIC[lang] || GENERIC.en, isNew: false };
    const [what, click] = e[lang] || e.en;
    const effect = (EFFECTS[e.effect] || EFFECTS.reads)[lang] || EFFECTS[e.effect].en;
    if (level === "short") return { text: what + ": " + effect + ".", isNew: false };
    return { text: NEW_MARK[lang] + what + " " + click + " (" + effect + ")", isNew: true };
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
  function suggestText(key, lang) {
    const name = (SUGGEST_NAMES[key] || {})[lang] || ((HOVER[key] || {})[lang] || [key])[0];
    return SUGGEST_LINE[lang] + name;
  }

  // WORK while anything needs attention; PLAY only after 20 s of quiet
  const WORK_STATES = ["scan_running", "proposals_not_approved", "approved_not_applied", "applied_not_retested"];
  function mode(s) {
    if (s.scanRunning || s.typing || WORK_STATES.indexOf(s.state) >= 0) return "work";
    if (s.sinceHoverMs < 10000 || s.idleMs < 20000) return "work";
    return "play";
  }
  const ANTICS = ["wink", "dance", "look", "peek", "hide", "chase"];
  const ANTIC_GAP_MS = 25000;
  function nextAntic(s) {
    if (s.mode !== "play" || s.reduced || s.now - (s.lastAnticAt || 0) < ANTIC_GAP_MS) return null;
    return ANTICS[(s.count || 0) % ANTICS.length];
  }

  // post-scan tour: score, then each finding in the order given (the code's severity order), then Fix first,
  // then the approve step. Only numbers from the inputs appear; no reassurance, no time or cost estimates.
  const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  function tourStops(input, lang) {
    const hi = lang === "hinglish";
    const findings = (input.findings || []).slice().sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
    const stops = [];
    if (!findings.length) {
      stops.push({ target: "score-card", expression: "proud", text: hi
        ? "Score dekho: chalaye gaye checks mein koi issue detect nahi hua. Ye sirf in checks ka result hai."
        : "Here's your score: no issues detected in the checks run. That covers only these checks." });
      return stops;
    }
    const worst = findings[0].severity;
    stops.push({ target: "score-card", expression: worst === "critical" || worst === "high" ? "nervous" : "curious",
      text: hi ? "Ye score ring hai: " + input.scoreLine + ". Checks ne " + findings.length + " issue(s) dhoondhe. Chalo ek ek karke dekhte hain."
        : "This is the score ring: " + input.scoreLine + ". The checks found " + findings.length + " issue(s). Let's go through them one by one." });
    const cap = (x) => String(x).charAt(0).toUpperCase() + String(x).slice(1);
    findings.forEach((f, i) => {
      const first = i === 0 ? (hi ? " Sabse pehle isse dekhte hain: evidence line padho." : " Look at this one first: read its evidence line.") : "";
      stops.push({ target: f.target, severity: f.severity, expression: SEV_RANK[f.severity] <= 1 ? "alert" : "curious",
        text: (hi ? cap(f.severity) + " wala issue (" + (i + 1) + "/" + findings.length + "): " + f.title + ", " + f.location + " par. "
          : cap(f.severity) + " finding (" + (i + 1) + " of " + findings.length + "): " + f.title + " at " + f.location + ". ") + f.plain + first });
    });
    stops.push({ target: "fixfirst-panel", expression: "determined", text: hi
      ? "Ye Fix first cards hain: sabse zaroori teen kaam, severity table ke order mein."
      : "These Fix first cards are the three most important fixes, in the severity table's order." });
    stops.push({ target: "proposals-panel", expression: "determined", text: hi
      ? "Yahan fix proposals hain. Approve karna aapka decision hai; main kabhi aapki jagah approve nahi karti."
      : "Here are the fix proposals. Approving is your decision; I never approve for you." });
    return stops;
  }

  const api = { EFFECTS, HOVER, GENERIC, SUGGEST, keyFor, detailFor, hoverText, bump, pickSuggestion, suggestText,
    mode, nextAntic, ANTICS, ANTIC_GAP_MS, tourStops, WORK_STATES };
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  if (typeof window !== "undefined") window.MayaBrain = api;
})();
