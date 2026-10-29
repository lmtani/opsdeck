// Sharing: pick cards, filters or feeds and export them to a file or the
// clipboard, or import an export after reviewing each item. One module for
// the three views; each registers its list, its ⇅ button and how to reload.
// The backend checks every item (POST /api/share/check), so the rules
// (names, conflicts, validity) are never re-implemented here.

import { api } from "./api.js";
import { closeModal, escapeHtml, openModal, toast } from "./ui.js";

const TYPES = {
  link: { label: "Links", one: "link", many: "links", exportKey: "links" },
  filter: { label: "GCP Logs", one: "filter", many: "filters", exportKey: "filters" },
  feed: { label: "GitHub feeds", one: "feed", many: "feeds", exportKey: "feeds" },
};
const ORDER = ["link", "filter", "feed"];

const views = {}; // type -> { list, itemSelector, reload }
let picking = null; // { type, ids: Set } while the selection bar is open
let review = null; // { bundle, checks, choices: Map(key -> { on, action }) }
let menu = null;

const bar = () => document.getElementById("share-bar");
const modalBody = () => document.getElementById("share-body");
const keyOf = (check) => `${check.type}:${check.index}`;

function count(n, type) {
  return `${n} ${n === 1 ? TYPES[type].one : TYPES[type].many}`;
}

// ----- menu ------------------------------------------------------------

function closeMenu() {
  menu?.remove();
  menu = null;
}

function openMenu(button, type) {
  closeMenu();
  menu = document.createElement("div");
  menu.className = "share-pop";
  menu.innerHTML = `
    <button type="button" data-act="export">↓ Export ${TYPES[type].many}…
      <small>pick which ones, then save a file or copy it</small></button>
    <button type="button" data-act="import">↑ Import…
      <small>an OpsDeck export: links, filters and feeds</small></button>`;
  menu.querySelector('[data-act="export"]').addEventListener("click", () => {
    closeMenu();
    startPicking(type);
  });
  menu.querySelector('[data-act="import"]').addEventListener("click", () => {
    closeMenu();
    openImport();
  });
  document.body.appendChild(menu);
  const rect = button.getBoundingClientRect();
  const left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 16);
  menu.style.left = `${Math.max(16, left)}px`;
  menu.style.top = `${rect.bottom + 8}px`;
}

// ----- export: picking items in the view's own list ---------------------

function items(type) {
  const { list, itemSelector } = views[type];
  return [...list.querySelectorAll(itemSelector)].filter((item) => item.dataset.id);
}

function paint() {
  for (const type of ORDER) {
    const view = views[type];
    if (!view) continue;
    const on = picking?.type === type;
    view.list.classList.toggle("is-picking", on);
    for (const item of items(type)) {
      const picked = on && picking.ids.has(item.dataset.id);
      item.classList.toggle("is-picked", picked);
      if (on) item.setAttribute("aria-pressed", String(picked));
      else item.removeAttribute("aria-pressed");
    }
  }
  if (!picking) return;
  const n = picking.ids.size;
  document.getElementById("share-bar-count").textContent = n
    ? `${count(n, picking.type)} selected`
    : `Click the ${TYPES[picking.type].many} to export`;
  for (const id of ["share-copy-btn", "share-save-btn"]) {
    document.getElementById(id).disabled = n === 0;
  }
}

function startPicking(type) {
  if (items(type).length === 0) {
    toast(`No ${TYPES[type].many} to export yet`);
    return;
  }
  picking = { type, ids: new Set() };
  bar().hidden = false;
  paint();
}

function stopPicking() {
  if (!picking) return;
  picking = null;
  bar().hidden = true;
  paint();
}

function toggle(item) {
  const { ids } = picking;
  if (ids.has(item.dataset.id)) ids.delete(item.dataset.id);
  else ids.add(item.dataset.id);
  paint();
}

async function exportPicked() {
  const { type, ids } = picking;
  const bundle = await api.exportShare({ [TYPES[type].exportKey]: [...ids] });
  return { type, n: ids.size, text: JSON.stringify(bundle, null, 2) };
}

async function savePicked() {
  try {
    const { type, n, text } = await exportPicked();
    const day = new Date().toISOString().slice(0, 10);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    link.download = `opsdeck-${TYPES[type].many}-${day}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    stopPicking();
    toast(`${count(n, type)} exported`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function copyPicked() {
  try {
    const { type, n, text } = await exportPicked();
    await navigator.clipboard.writeText(text);
    stopPicking();
    toast(`${count(n, type)} copied: paste them into Import on the other OpsDeck`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

// ----- import: read a file, review each item, write --------------------

function openImport() {
  stopPicking();
  review = null;
  renderSource();
  openModal("share-modal");
}

function renderSource() {
  document.getElementById("share-modal-title").textContent = "Import";
  modalBody().innerHTML = `
    <div class="share-drop" id="share-drop">
      <span class="share-drop-mark">↑</span>
      <strong>Drop an OpsDeck export here</strong>
      <span>or <button type="button" class="share-link-btn" id="share-pick-file">choose a file</button>,
        or paste it with <kbd>Ctrl</kbd>+<kbd>V</kbd></span>
    </div>
    <p class="field-hint">You review every item before anything is saved.</p>
    <div class="modal-actions">
      <button type="button" class="btn btn-ghost" data-share-close>Cancel</button>
    </div>`;
  const drop = document.getElementById("share-drop");
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("is-over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("is-over");
    readFile(event.dataTransfer.files[0]);
  });
  document
    .getElementById("share-pick-file")
    .addEventListener("click", () => document.getElementById("share-file").click());
}

async function readFile(file) {
  if (file) await readText(await file.text());
}

async function readText(text) {
  let bundle;
  try {
    bundle = JSON.parse(text);
  } catch {
    toast("This is not an OpsDeck export (invalid JSON)", "error");
    return;
  }
  if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) {
    toast("This is not an OpsDeck export", "error");
    return;
  }
  try {
    const checks = await api.checkShare(bundle);
    const choices = new Map(
      checks.map((check) => [
        keyOf(check),
        // a command card runs on this machine: the user opts in explicitly
        { on: check.status !== "invalid" && !check.command, action: "create" },
      ])
    );
    review = { bundle, checks, choices };
    if (document.getElementById("share-modal").hidden) openModal("share-modal");
    renderReview();
  } catch (error) {
    toast(error.message, "error");
  }
}

function statusHtml(check, choice) {
  if (check.status === "invalid") return `<span class="share-badge is-invalid">invalid</span>`;
  if (check.status === "new") return `<span class="share-badge">new</span>`;
  const button = (action, label, title) =>
    `<button type="button" data-action="${action}" aria-pressed="${choice.action === action}"
      title="${title}">${label}</button>`;
  return `
    <span class="share-conflict">name in use</span>
    <span class="gh-seg share-seg" role="group" aria-label="When the name is in use">
      ${button("create", "keep both", "Import as a new item with a numbered name")}
      ${button("replace", "replace", "Overwrite the existing item with this one")}
    </span>`;
}

function rowHtml(check) {
  const choice = review.choices.get(keyOf(check));
  const invalid = check.status === "invalid";
  return `
    <div class="share-row${invalid ? " is-invalid" : ""}${choice.on ? "" : " is-off"}" data-key="${keyOf(check)}">
      <label class="share-pick">
        <input type="checkbox" ${choice.on ? "checked" : ""} ${invalid ? "disabled" : ""} />
        <span class="share-main">
          <span class="share-name">${escapeHtml(check.name || `#${check.index + 1}`)}</span>
          ${check.detail && !check.command ? `<span class="share-detail">${escapeHtml(check.detail)}</span>` : ""}
          ${
            check.command
              ? `<span class="share-warn">⚠ runs a command on this machine:
                  <code>${escapeHtml(check.command)}</code></span>`
              : ""
          }
          ${invalid ? `<span class="share-error">${escapeHtml(check.error)}</span>` : ""}
        </span>
      </label>
      <span class="share-side">${statusHtml(check, choice)}</span>
    </div>`;
}

function renderReview() {
  const { checks, choices } = review;
  const chosen = [...choices.values()].filter((choice) => choice.on).length;
  const conflicts = checks.filter((check) => check.status === "conflict").length;
  const groups = ORDER.map((type) => {
    const rows = checks.filter((check) => check.type === type);
    if (!rows.length) return "";
    return `
      <section class="share-group">
        <h3 class="share-group-title">${TYPES[type].label} <span>${rows.length}</span></h3>
        ${rows.map(rowHtml).join("")}
      </section>`;
  }).join("");

  document.getElementById("share-modal-title").textContent = "Import";
  modalBody().innerHTML = `
    <p class="share-summary">${checks.length} item${checks.length === 1 ? "" : "s"} in this file${
      conflicts ? ` · ${conflicts} with a name already in use` : ""
    }</p>
    ${checks.length ? `<div class="share-review">${groups}</div>` : `<p class="field-hint">The file is empty.</p>`}
    <div class="modal-actions">
      <button type="button" class="btn btn-ghost share-back" data-share-back>← Another file</button>
      <button type="button" class="btn btn-ghost" data-share-close>Cancel</button>
      <button type="button" class="btn btn-primary" id="share-import-btn" ${chosen ? "" : "disabled"}>
        Import ${chosen || ""}</button>
    </div>`;

  for (const row of modalBody().querySelectorAll(".share-row")) {
    const choice = choices.get(row.dataset.key);
    row.querySelector("input").addEventListener("change", (event) => {
      choice.on = event.target.checked;
      renderReview();
    });
    for (const button of row.querySelectorAll("[data-action]")) {
      button.addEventListener("click", () => {
        choice.action = button.dataset.action;
        choice.on = true;
        renderReview();
      });
    }
  }
  modalBody()
    .querySelector("[data-share-back]")
    .addEventListener("click", () => {
      review = null;
      renderSource();
    });
  document.getElementById("share-import-btn").addEventListener("click", runImport);
}

async function runImport() {
  const button = document.getElementById("share-import-btn");
  button.disabled = true;
  const choices = review.checks
    .filter((check) => review.choices.get(keyOf(check)).on)
    .map((check) => ({
      type: check.type,
      index: check.index,
      action: review.choices.get(keyOf(check)).action,
    }));
  try {
    const result = await api.importShare({ bundle: review.bundle, choices });
    closeModal("share-modal");
    review = null;
    const touched = [...result.created, ...result.replaced];
    const types = ORDER.filter((type) => touched.some((item) => item.type === type));
    await Promise.all(types.map((type) => views[type]?.reload()));
    flash(touched);
    const summary = (list) =>
      ORDER.filter((type) => list.some((item) => item.type === type))
        .map((type) => count(list.filter((item) => item.type === type).length, type))
        .join(", ");
    const said = [
      result.created.length && `imported ${summary(result.created)}`,
      result.replaced.length && `replaced ${summary(result.replaced)}`,
    ].filter(Boolean);
    toast(
      said.join(" · ").replace(/^./, (c) => c.toUpperCase()),
      "success"
    );
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}

// the imported items glow once in their lists
function flash(touched) {
  for (const { type, id } of touched) {
    const item = views[type]?.list.querySelector(`[data-id="${CSS.escape(id)}"]`);
    if (!item) continue;
    item.classList.add("is-imported");
    const done = (event) => {
      if (event.animationName !== "share-imported") return;
      item.classList.remove("is-imported");
      item.removeEventListener("animationend", done);
    };
    item.addEventListener("animationend", done);
  }
}

// ----- wiring ----------------------------------------------------------

// A view's list of shareable items: `button` opens the export/import menu,
// clicks on `itemSelector` elements (with a data-id) pick them while
// exporting, and `reload` re-reads the list after an import.
export function registerShare({ type, button, list, itemSelector, reload }) {
  views[type] = { list, itemSelector, reload };
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (menu) closeMenu();
    else openMenu(button, type);
  });

  // capture phase: while picking, a click selects instead of opening the item
  const intercept = (event) => {
    if (picking?.type !== type) return;
    const item = event.target.closest(itemSelector);
    if (!item?.dataset.id || !list.contains(item)) return;
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    toggle(item);
  };
  list.addEventListener("click", intercept, true);
  list.addEventListener("keydown", intercept, true);
  list.addEventListener("dragstart", (event) => picking?.type === type && event.preventDefault());
  // views re-render their lists (polling, search): keep the picked look
  new MutationObserver(() => picking?.type === type && paint()).observe(list, { childList: true });
}

export function initShare() {
  document.getElementById("share-all-btn").addEventListener("click", () => {
    for (const item of items(picking.type)) picking.ids.add(item.dataset.id);
    paint();
  });
  document.getElementById("share-none-btn").addEventListener("click", () => {
    picking.ids.clear();
    paint();
  });
  document.getElementById("share-cancel-btn").addEventListener("click", stopPicking);
  document.getElementById("share-copy-btn").addEventListener("click", copyPicked);
  document.getElementById("share-save-btn").addEventListener("click", savePicked);

  const file = document.getElementById("share-file");
  file.addEventListener("change", async () => {
    await readFile(file.files[0]);
    file.value = "";
  });
  document.getElementById("share-modal").addEventListener("click", (event) => {
    if (event.target.closest("[data-share-close]")) closeModal("share-modal");
  });

  // paste an export while the import modal waits for one
  document.addEventListener("paste", (event) => {
    if (document.getElementById("share-modal").hidden || review) return;
    const text = event.clipboardData?.getData("text");
    if (text) readText(text);
  });
  // a .json file dropped anywhere on the page goes to the import review
  // (instead of the browser navigating to it)
  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    const dropped = event.dataTransfer?.files[0];
    if (!dropped || event.defaultPrevented) return;
    event.preventDefault();
    if (dropped.name.endsWith(".json")) readFile(dropped);
    else toast("Drop an OpsDeck export (.json) to import it", "error");
  });

  document.addEventListener("click", (event) => {
    if (menu && !menu.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (menu) closeMenu();
    else if (picking && !document.querySelector(".modal-backdrop:not([hidden])")) stopPicking();
  });
  document.addEventListener("view:change", () => {
    closeMenu();
    stopPicking();
  });
}
