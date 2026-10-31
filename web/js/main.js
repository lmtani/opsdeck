// App bootstrap: tabs, clock, provider status, module init.

import { api } from "./api.js";
import { initGithub, setGithubAvailability } from "./github.js";
import { initLinks } from "./links.js";
import { initLogs } from "./logs.js";
import { initShare } from "./share.js";
import { initTheme } from "./theme.js";
import { LOCALE, setupConfirmModal, setupModalDismissal, toast } from "./ui.js";

// The tab in use is remembered, so a reload reopens it.
const TAB_KEY = "opsdeck.tab";

function savedTab() {
  try {
    return localStorage.getItem(TAB_KEY);
  } catch {
    return null; // storage blocked: open on the default tab
  }
}

// Returns a function that announces the active tab with `view:change`.
function setupTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  const indicator = document.getElementById("tab-indicator");

  function moveIndicator(tab) {
    indicator.style.left = `${tab.offsetLeft}px`;
    indicator.style.width = `${tab.offsetWidth}px`;
  }

  // lets views react to being shown or left (e.g. GitHub marks feeds as seen)
  const announce = (tab) =>
    document.dispatchEvent(new CustomEvent("view:change", { detail: tab.dataset.view }));

  function show(tab) {
    for (const item of tabs) item.classList.toggle("is-active", item === tab);
    moveIndicator(tab);
    document.querySelectorAll(".view").forEach((view) => {
      view.hidden = view.id !== `view-${tab.dataset.view}`;
    });
    try {
      localStorage.setItem(TAB_KEY, tab.dataset.view);
    } catch {
      /* not remembered: the next load opens the default tab */
    }
  }

  const activeTab = () => tabs.find((tab) => tab.classList.contains("is-active"));
  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      show(tab);
      announce(tab);
    });
  }
  // reopen the tab of the last visit right away, so the default one never flashes
  const remembered = tabs.find((tab) => tab.dataset.view === savedTab());
  if (remembered) show(remembered);
  // a tab changes size when the display font loads or the GitHub badge shows,
  // which also moves the tabs after it
  const observer = new ResizeObserver(() => moveIndicator(activeTab()));
  for (const tab of tabs) observer.observe(tab);
  return () => announce(activeTab());
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
    // null: no log source yet, so there is nothing to report
    document.getElementById("logs-status").hidden = health.logs === null;
    showStatus(
      "logs-status",
      health.logs,
      health.logs ? "Log sources can be queried" : "Some log source cannot be queried"
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
  initShare();
  const announceTab = setupTabs();
  setupClock();
  checkHealth();
  document.addEventListener("logs:sources", checkHealth);

  await initTheme();
  try {
    await Promise.all([initLinks(), initLogs(), initGithub()]);
  } catch (error) {
    toast(`Could not load data: ${error.message}`, "error");
  }
  // the views listen once initialized: tell them which one a reload reopened
  announceTab();
}

start();
