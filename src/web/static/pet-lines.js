"use strict";
// Fixed lines the MAYA pet says in the browser (greeting, chips, small reactions). Never from a model, so they
// carry no model-text badge. MAYA speaks as herself, warm and in the first person.
// HINGLISH and DEVANAGARI strings below NEED NATIVE-SPEAKER REVIEW before they are relied on. The Devanagari
// ("spoken_hi") text is only used with a hi-IN voice; the screen always shows the Roman text.
(function () {
  const LINES = {
    greeting: {
      en: "Hii! I'm MAYA. I'll help you understand and check your website's security, one small step at a time.",
      hinglish: "Hii! Main MAYA hoon. Aapki website ki security samjhane aur check karne mein main help karungi.",
      spoken_hi: "हाय! मैं माया हूँ। आपकी वेबसाइट की सिक्योरिटी समझाने और चेक करने में मैं मदद करूँगी।",
    },
    askLevel: {
      en: "First, how much do you know about security?",
      hinglish: "Pehle batao, security ke baare mein aap kitna jaante ho?",
      spoken_hi: "पहले बताओ, सिक्योरिटी के बारे में आप कितना जानते हो?",
    },
    levelSet: {
      en: "Got it! I'll explain things that way. You can change it any time in Pet settings.",
      hinglish: "Done! Main isi tarah samjhaungi. Pet settings mein kabhi bhi badal sakte ho.",
      spoken_hi: "डन! मैं इसी तरह समझाऊँगी। पेट सेटिंग्स में कभी भी बदल सकते हो।",
    },
    askVoice: {
      en: "Want me to read the steps out loud?",
      hinglish: "Kya main steps bolkar sunaun?",
      spoken_hi: "क्या मैं स्टेप्स बोलकर सुनाऊँ?",
    },
    voiceTest: {
      en: "Hii, I'm MAYA. This is how I sound.",
      hinglish: "Hii, main MAYA hoon. Meri awaaz aisi hai.",
      spoken_hi: "हाय, मैं माया हूँ। मेरी आवाज़ ऐसी है।",
    },
    error: {
      en: "Oops, that didn't work. The message on the screen says what happened.",
      hinglish: "Oho, ye nahi hua. Screen par message mein likha hai kya hua.",
      spoken_hi: "ओहो, ये नहीं हुआ। स्क्रीन पर मैसेज में लिखा है क्या हुआ।",
    },
    hidden: {
      en: "Okay, I'll stay out of the way. Press Call pet when you need me.",
      hinglish: "Theek hai, main side mein rehti hoon. Zaroorat ho to Call pet dabana.",
      spoken_hi: "ठीक है, मैं साइड में रहती हूँ। ज़रूरत हो तो कॉल पेट दबाना।",
    },
  };
  const CHIPS = {
    show: { en: "Show me", hinglish: "Dikhao" },
    next: { en: "Next tip", hinglish: "Agla tip" },
    levelNew: { en: "I'm new", hinglish: "Bilkul new" },
    levelSome: { en: "I know some", hinglish: "Thoda pata hai" },
    levelTech: { en: "I'm technical", hinglish: "Technical hoon" },
    voiceOn: { en: "Turn on voice", hinglish: "Awaaz on karo" },
    later: { en: "Not now", hinglish: "Abhi nahi" },
    tourStart: { en: "Show me", hinglish: "Dikhao" },
    skip: { en: "Skip", hinglish: "Skip" },
    nextStop: { en: "Next", hinglish: "Aage" },
    done: { en: "Done", hinglish: "Ho gaya" },
  };
  const api = { LINES: LINES, CHIPS: CHIPS };
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  if (typeof window !== "undefined") window.MayaLines = api;
})();
