// Backup and restore (the top-bar menu): the download is a plain link to
// GET /api/backup; a restore reads a file, asks the server what it holds,
// confirms that it replaces everything and reloads the page on success.

import { api } from "./api.js";
import { confirmDanger, LOCALE, toast } from "./ui.js";

const RESTORED_KEY = "opsdeck.backup.restored";

function plural(count, noun, pluralForm = `${noun}s`) {
  return `${count} ${count === 1 ? noun : pluralForm}`;
}

function describe(summary) {
  const parts = [
    plural(summary.links, "card"),
    plural(summary.log_sources, "log source"),
    plural(summary.log_filters, "filter"),
    plural(summary.github_feeds, "feed"),
  ];
  return `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

async function restoreFrom(file) {
  let backup;
  try {
    backup = JSON.parse(await file.text());
  } catch {
    toast(`${file.name} is not a JSON file`, "error");
    return;
  }
  let summary;
  try {
    summary = await api.checkBackup(backup);
  } catch (error) {
    toast(error.message, "error", 6000);
    return;
  }
  const when = summary.created_at
    ? ` from ${new Date(summary.created_at).toLocaleString(LOCALE, { hourCycle: "h23", dateStyle: "medium", timeStyle: "short" })}`
    : "";
  confirmDanger(
    `The backup${when} holds ${describe(summary)}. Restoring it replaces every card, log source, filter, feed and setting here. Download a backup first if you may want them back.`,
    async () => {
      try {
        const restored = await api.restoreBackup(backup);
        try {
          sessionStorage.setItem(RESTORED_KEY, describe(restored));
        } catch {
          /* the toast after the reload is a nicety */
        }
        window.location.reload(); // every view, the theme and the intervals come back from it
      } catch (error) {
        toast(error.message, "error", 6000);
      }
    },
    { title: "Restore backup?", confirmLabel: "Replace everything" }
  );
}

export function initBackup() {
  const button = document.getElementById("backup-btn");
  const pop = document.getElementById("backup-pop");
  const input = document.getElementById("backup-file");
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    pop.hidden = !pop.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!pop.hidden && !pop.contains(event.target)) pop.hidden = true;
  });
  document.getElementById("backup-download").addEventListener("click", () => {
    pop.hidden = true;
  });
  document.getElementById("backup-restore-btn").addEventListener("click", () => {
    pop.hidden = true;
    input.click();
  });
  input.addEventListener("change", () => {
    const [file] = input.files;
    input.value = ""; // the same file can be picked again
    if (file) restoreFrom(file);
  });

  let restored = null;
  try {
    restored = sessionStorage.getItem(RESTORED_KEY);
    sessionStorage.removeItem(RESTORED_KEY);
  } catch {
    /* storage blocked */
  }
  if (restored) toast(`Backup restored: ${restored}`, "success", 5000);
}
