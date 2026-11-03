// A small menu of choices under a button (the poll-menu look): one button per
// choice, the current one marked. It lives in <body>, so clicks in it never
// reach the card the button sits on. Closes on a pick, a click elsewhere or Esc.

import { hideTooltip } from "./tooltip.js";
import { escapeHtml } from "./ui.js";

let close = null;

// choices: [{value, label, icon}] (icon: trusted markup before the label);
// `suggested` is outlined and focused when nothing better is current;
// choose(value) runs after the menu closed.
export function openChoiceMenu(anchor, { label, choices, current, suggested = null, choose }) {
  const reopening = close?.anchor === anchor;
  close?.();
  if (reopening) return; // a second click on the same button just closes it
  hideTooltip();
  const popup = document.createElement("div");
  popup.className = "poll-menu choice-menu";
  popup.setAttribute("role", "menu");
  popup.setAttribute("aria-label", label);
  popup.innerHTML =
    `<span class="choice-menu-label">${escapeHtml(label)}</span>` +
    choices
      .map(
        ({ value, label: text, icon = "" }) =>
          `<button type="button" role="menuitemradio" aria-checked="${value === current}" ` +
          `class="${value === current ? "is-on" : value === suggested ? "is-suggested" : ""}" ` +
          `data-value="${escapeHtml(value)}">${icon}${escapeHtml(text)}</button>`
      )
      .join("");

  const outside = (event) => {
    if (!popup.contains(event.target) && !anchor.contains(event.target)) close();
  };
  const onKey = (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation(); // Esc closes the menu, not the view behind it
    close();
    anchor.focus();
  };
  close = () => {
    popup.remove();
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("scroll", close, true);
    close = null;
  };
  close.anchor = anchor;

  popup.addEventListener("click", (event) => {
    const button = event.target.closest("[data-value]");
    if (!button) return;
    close();
    choose(button.dataset.value);
  });
  document.body.appendChild(popup);
  const box = anchor.getBoundingClientRect();
  const width = popup.offsetWidth;
  popup.style.position = "fixed";
  popup.style.top = `${box.bottom + 4}px`;
  popup.style.left = `${Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8))}px`;
  (popup.querySelector(".is-suggested") || popup.querySelector(".is-on, button"))?.focus();
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("scroll", close, true);
}
