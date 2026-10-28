"use strict";
// Printable report: the button opens the browser's print dialog (choose "Save as PDF" there).
document.addEventListener("DOMContentLoaded", () => {
  const b = document.getElementById("print-btn");
  if (b) b.addEventListener("click", () => window.print());
});
