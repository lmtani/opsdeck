// App bootstrap: tabs, clock, provider status, module init.

import { api } from "./api.js";
import { initGithub, setGithubAvailability } from "./github.js";
import { initLinks } from "./links.js";
import { initLogs } from "./logs.js";
import { initTheme } from "./theme.js";
import { LOCALE, setupConfirmModal, setupModalDismissal, toast } from "./ui.js";

function setupTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  const indicator = document.getElementById("tab-indicator");

  function moveIndicator(tab) {
    indicator.style.left = `${tab.offsetLeft}px`;
    indicator.style.width = `${tab.offsetWidth}px`;
  }

  function activate(tab) {
    for (const item of tabs) item.classList.toggle("is-active", item === tab);
    moveIndicator(tab);
    document.querySelectorAll(".view").forEach((view) => {
      view.hidden = view.id !== `view-${tab.dataset.view}`;
    });
    // lets views react to being shown or left (e.g. GitHub marks feeds as seen)
    document.dispatchEvent(new CustomEvent("view:change", { detail: tab.dataset.view }));
  }

  for (const tab of tabs) tab.addEventListener("click", () => activate(tab));
  requestAnimationFrame(() => moveIndicator(tabs[0]));
  window.addEventListener("resize", () =>
    moveIndicator(tabs.find((tab) => tab.classList.contains("is-active")))
  );
}

function setupClock() {
  const timeNode = document.getElementById("clock-time");
  const dateNode = document.getElementById("clock-date");
  function tick() {
    const now = new Date();
    timeNode.textContent = now.toLocaleTimeString(LOCALE, { hourCycle: "h23" });
    dateNode.textContent = now.toLocaleDateString(LOCALE, {
      weekday: "short",
      day: "2-digit",
      month: "short",
    });
  }
  tick();
  setInterval(tick, 1000);
}

function showStatus(id, ok, title) {
  const status = document.getElementById(id);
  status.classList.toggle("is-ok", ok);
  status.classList.toggle("is-down", !ok);
  status.title = title;
}

async function checkHealth() {
  try {
    const health = await api.health();
    showStatus(
      "logs-status",
      health.logs,
      health.logs ? "Log provider available" : "Log provider unavailable"
    );
    showStatus(
      "github-status",
      health.github,
      health.github ? "GitHub token configured" : "No GitHub token (GITHUB_TOKEN or gh auth login)"
    );
    setGithubAvailability(health.github);
  } catch {
    showStatus("logs-status", false, "Backend unavailable");
    showStatus("github-status", false, "Backend unavailable");
  }
}

// Makes the app installable as a PWA with an offline notice; see sw.js. Browsers only allow
// service workers in a secure context (HTTPS or localhost).
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").catch(() => {
    /* not a secure context: the app still works, it just cannot be installed */
  });
}

async function start() {
  registerServiceWorker();
  setupModalDismissal();
  setupConfirmModal();
  setupTabs();
  setupClock();
  checkHealth();

  await initTheme();
  try {
    await Promise.all([initLinks(), initLogs(), initGithub()]);
  } catch (error) {
    toast(`Could not load data: ${error.message}`, "error");
  }
}

start();
