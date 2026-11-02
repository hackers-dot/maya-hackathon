"use strict";
// Test-only contact sheet (served only in --ui-preview mode): every expression and every pose of the pet.
document.addEventListener("DOMContentLoaded", () => {
  const P = window.MayaPet;
  const cell = (parent, label, opts) => {
    const box = document.createElement("div");
    box.className = "pet-cell";
    parent.appendChild(box);
    P.create(box, Object.assign({ loops: false, instant: true }, opts));
    const code = document.createElement("code");
    code.textContent = label;
    box.appendChild(code);
  };
  const hero = document.getElementById("sheet-hero");
  const big = document.createElement("div");
  big.className = "hero-pet";
  hero.appendChild(big);
  P.create(big, { expression: "happy", pose: "idle", loops: false, instant: true });
  for (const name of Object.keys(P.core.EXPRESSIONS)) cell(document.getElementById("sheet-expressions"), name, { expression: name });
  for (const pose of P.core.POSES) cell(document.getElementById("sheet-poses"), pose, { expression: "happy", pose: pose });
});
