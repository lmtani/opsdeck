// Theme switching: applies data-theme, persists to backend + localStorage.

import { api } from "./api.js";

const THEMES = [
  { id: "midnight", label: "Midnight", swatch: "#41d6e3", base: "#0d1322" },
  { id: "phosphor", label: "Phosphor", swatch: "#51e07f", base: "#0a120b" },
  { id: "ember", label: "Ember", swatch: "#ffae42", base: "#1b130d" },
  { id: "paper", label: "Paper", swatch: "#0e7a72", base: "#fbf9f4" },
];

function apply(themeId) {
  document.documentElement.dataset.theme = themeId;
  localStorage.setItem("opsdeck-theme", themeId);
  renderOptions();
}

function renderOptions() {
  const pop = document.getElementById("theme-pop");
  const current = document.documentElement.dataset.theme;
  pop.innerHTML = "";
  for (const theme of THEMES) {
    const button = document.createElement("button");
    button.className = `theme-option${theme.id === current ? " is-active" : ""}`;
    button.innerHTML = `<span class="swatch" style="background:linear-gradient(135deg, ${theme.base} 50%, ${theme.swatch} 50%)"></span>${theme.label}`;
    button.addEventListener("click", async () => {
      apply(theme.id);
      pop.hidden = true;
      try {
        await api.saveSettings({ theme: theme.id });
      } catch {
        /* backend offline: localStorage still keeps the choice */
      }
    });
    pop.appendChild(button);
  }
}

export async function initTheme() {
  const cached = localStorage.getItem("opsdeck-theme");
  if (cached) apply(cached);

  const button = document.getElementById("theme-btn");
  const pop = document.getElementById("theme-pop");
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    pop.hidden = !pop.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!pop.hidden && !pop.contains(event.target)) pop.hidden = true;
  });

  renderOptions();

  try {
    const settings = await api.getSettings();
    if (settings.theme && settings.theme !== document.documentElement.dataset.theme) {
      apply(settings.theme);
    }
  } catch {
    /* keep local theme */
  }
}
