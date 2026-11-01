// The update notice: GET /api/version says which version runs and whether a
// newer release is out. The brand's tooltip names the running version; a
// newer release shows a pill in the top bar whose menu links to its notes and
// gives the command that updates. The backend reads GitHub a few times a day,
// so asking it every hour costs nothing.

import { api } from "./api.js";
import { escapeHtml, LOCALE, toast } from "./ui.js";

const RECHECK_MS = 60 * 60 * 1000;
const UPGRADE = "uv tool upgrade opsdeck";
const UPGRADE_SERVICE = `${UPGRADE} && opsdeck service restart`;
const REINSTALL = "uv tool install --force git+https://github.com/lmtani/opsdeck@stable";
const IMAGE = "ghcr.io/lmtani/opsdeck:latest";

function render(info) {
  document.querySelector(".brand").title = `OpsDeck ${info.current}`;
  const box = document.getElementById("update");
  box.hidden = !info.newer;
  if (!info.newer) return;
  document.getElementById("update-btn").textContent = `↑ ${info.latest}`;
  const when = info.published_at
    ? ` · released ${new Date(info.published_at).toLocaleDateString(LOCALE, { dateStyle: "medium" })}`
    : "";
  const upgrade = info.service ? UPGRADE_SERVICE : UPGRADE;
  const after = info.service
    ? "The service restarts on the new version; the database migrates itself."
    : "Then restart <code>opsdeck</code>; the database migrates itself.";
  document.getElementById("update-pop").innerHTML = `
    <p class="update-title">OpsDeck ${escapeHtml(info.latest)} is out</p>
    <p class="update-sub">You have ${escapeHtml(info.current)}${escapeHtml(when)}</p>
    <a class="theme-option" href="${escapeHtml(info.url)}" target="_blank" rel="noopener">Release notes ↗</a>
    <button type="button" class="update-command" data-copy="${upgrade}" title="Copy">
      <code>${upgrade}</code><span aria-hidden="true">⧉</span>
    </button>
    <p class="update-note">${after} Installed from a release tag, which <code>upgrade</code> keeps? Switch to <code>@stable</code> once:</p>
    <button type="button" class="update-command" data-copy="${REINSTALL}" title="Copy">
      <code>…/opsdeck@stable --force</code><span aria-hidden="true">⧉</span>
    </button>
    <p class="update-note">In a container, pull <code>${IMAGE}</code> instead.</p>`;
}

async function check() {
  try {
    render(await api.version());
  } catch {
    /* the notice is a nicety: a backend without it just shows nothing */
  }
}

export function initUpdate() {
  const button = document.getElementById("update-btn");
  const pop = document.getElementById("update-pop");
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    pop.hidden = !pop.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!pop.hidden && !pop.contains(event.target)) pop.hidden = true;
  });
  pop.addEventListener("click", async (event) => {
    const copy = event.target.closest("[data-copy]");
    if (!copy) return;
    try {
      await navigator.clipboard.writeText(copy.dataset.copy);
      toast("Command copied");
    } catch {
      toast("Could not copy: select the command instead", "error");
    }
  });
  check();
  setInterval(() => {
    if (document.visibilityState === "visible") check();
  }, RECHECK_MS);
}
