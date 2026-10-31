// Background rounds on a schedule, and the thin line that shows it: the line
// fills up until the next round and its tooltip says when that is and how
// often rounds run; clicking it picks how often. Used by the watched log
// filters, the GitHub feed badges and the auto refresh of the open feed.

import { hideTooltip, setupTooltips, tooltipContent } from "./tooltip.js";
import { escapeHtml, LOCALE } from "./ui.js";

const minutes = (ms) =>
  ms >= 60_000 ? `${Math.round(ms / 60_000)} min` : `${Math.round(ms / 1000)} s`;

function remaining(ms) {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

// `line` is an element holding one <i> (the fill). `state()` answers what the
// rounds should do now: {every: ms} to run that often, {paused: "why"} to hold
// (the line stays, dimmed), or null when there is nothing to poll (hidden).
// `run` does a round; any round, scheduled or not (a manual refresh, a new
// watch), calls `ran()` so the next one counts from it. `schedule()` re-reads
// the state after something it depends on changed. `menu` makes the line a
// button that offers `choices()` (seconds, from the backend settings) and
// calls `choose(seconds)`; the state then answers with the new interval.
export function createPoller({ line, what, state, run, menu = null }) {
  const fill = line.querySelector("i");
  let last = Date.now();
  let timer = null;
  let current = null;

  function paint() {
    line.hidden = !current;
    if (!current) return;
    line.classList.toggle("is-paused", !current.every);
    if (!current.every) return;
    // restart the fill where the countdown is, so a repaint does not jump
    fill.style.animation = "none";
    void fill.offsetWidth;
    fill.style.animation = "";
    fill.style.animationDuration = `${current.every}ms`;
    fill.style.animationDelay = `${-Math.min(Date.now() - last, current.every)}ms`;
  }

  function schedule() {
    clearTimeout(timer);
    current = state();
    paint();
    if (!current?.every) return;
    timer = setTimeout(
      () => {
        ran();
        run();
      },
      Math.max(0, last + current.every - Date.now())
    );
  }

  function ran() {
    last = Date.now();
    schedule();
  }

  tooltipContent.set(line, () => {
    if (!current) return "";
    const lastAt = new Date(last).toLocaleTimeString(LOCALE, { hourCycle: "h23" });
    if (!current.every) {
      return (
        `<div class="gh-tooltip-main">${escapeHtml(what())}</div>` +
        `<div class="gh-tooltip-sub">${escapeHtml(current.paused)} · last at ${lastAt}</div>`
      );
    }
    const next = remaining(last + current.every - Date.now());
    return (
      `<div class="gh-tooltip-main">Next check in ${next}</div>` +
      `<div class="gh-tooltip-sub">${escapeHtml(what())} · every ${minutes(current.every)} · last at ${lastAt}</div>` +
      (menu ? '<div class="gh-tooltip-sub">click to change how often</div>' : "")
    );
  });
  line.dataset.tooltip = "";
  setupTooltips(line);
  if (menu) setupMenu(line, menu);
  // a hidden page may hold its rounds; back in view, an overdue one runs at once
  document.addEventListener("visibilitychange", schedule);

  return { schedule, ran };
}

// The interval picker under a line: one button per choice, the current one
// marked. Closes on a pick, a click elsewhere or Esc.
function setupMenu(line, menu) {
  line.tabIndex = 0;
  line.setAttribute("role", "button");
  line.setAttribute("aria-haspopup", "true");
  line.setAttribute("aria-label", "How often to check");
  let popup = null;

  function close() {
    popup?.remove();
    popup = null;
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", onKey, true);
  }
  function outside(event) {
    if (!popup?.contains(event.target) && !line.contains(event.target)) close();
  }
  function onKey(event) {
    if (event.key !== "Escape") return;
    event.stopPropagation(); // Esc closes the menu, not the view behind it
    close();
    line.focus();
  }
  function open() {
    hideTooltip();
    popup = document.createElement("div");
    popup.className = "poll-menu";
    popup.setAttribute("role", "menu");
    popup.setAttribute("aria-label", "Check every");
    popup.title = "Check every";
    const current = menu.current();
    popup.innerHTML = menu
      .choices()
      .map(
        (seconds) =>
          `<button type="button" role="menuitemradio" aria-checked="${seconds === current}" ` +
          `class="${seconds === current ? "is-on" : ""}" data-seconds="${seconds}">${minutes(seconds * 1000)}</button>`
      )
      .join("");
    popup.addEventListener("click", (event) => {
      const button = event.target.closest("[data-seconds]");
      if (!button) return;
      close();
      menu.choose(Number(button.dataset.seconds));
    });
    line.after(popup);
    // siblings share an offset parent: the menu opens right under the line
    popup.style.top = `${line.offsetTop + line.offsetHeight}px`;
    popup.style.left = `${line.offsetLeft}px`;
    popup.querySelector(".is-on, button")?.focus();
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", onKey, true);
  }
  const toggle = () => (popup ? close() : open());
  line.addEventListener("click", toggle);
  line.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    toggle();
  });
}
