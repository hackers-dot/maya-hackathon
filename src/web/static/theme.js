"use strict";
// Loaded synchronously in <head> so the saved theme applies before first paint. Dark is the default.
(function () {
  let saved = null;
  try { saved = window.localStorage.getItem("maya-theme"); } catch (e) { saved = null; }
  // ?theme=light|dark previews a theme without saving it (used for screenshots)
  const asked = new URLSearchParams(window.location.search).get("theme");
  const theme = asked === "light" || asked === "dark" ? asked : saved;
  document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
  // the slow background wave pauses while the tab is hidden (visual only)
  document.addEventListener("visibilitychange", function () {
    document.documentElement.classList.toggle("tab-hidden", document.hidden);
  });
})();
