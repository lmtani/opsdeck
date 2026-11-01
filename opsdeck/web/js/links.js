// Links view: card grid, search, category chips, create/edit modal.

import { api } from "./api.js";
import { makeSortable } from "./dnd.js";
import { registerShare } from "./share.js";
import { closeModal, confirmDanger, escapeHtml, openModal, toast } from "./ui.js";

let links = [];
let activeCategory = "";
let searchTerm = "";
let editingId = null;
let selectedAccent = "";

const grid = () => document.getElementById("link-grid");

// Preset covers selectable in the modal (link.accent stores the id).
const COVER_PRESETS = [
  { id: "crimson", label: "prod", colors: ["#7f1d2d", "#e0455e"] },
  { id: "amber", label: "staging", colors: ["#7c4a03", "#f5a623"] },
  { id: "emerald", label: "dev", colors: ["#14532d", "#34d399"] },
  { id: "azure", label: "infra", colors: ["#1e3a8a", "#38bdf8"] },
  { id: "violet", label: "data", colors: ["#4c1d95", "#a78bfa"] },
  { id: "teal", label: "api", colors: ["#134e4a", "#2dd4bf"] },
  { id: "rose", label: "alerts", colors: ["#831843", "#fb7185"] },
  { id: "slate", label: "util", colors: ["#1f2937", "#94a3b8"] },
];
const COVER_BY_ID = Object.fromEntries(COVER_PRESETS.map((p) => [p.id, p]));

function coverStyle(presetId) {
  const preset = COVER_BY_ID[presetId];
  if (!preset) return null;
  return (
    "background:" +
    "repeating-linear-gradient(45deg, rgba(255,255,255,0.05) 0 2px, transparent 2px 9px), " +
    `linear-gradient(135deg, ${preset.colors[0]}, ${preset.colors[1]})`
  );
}

// Deterministic gradient for cards without an image or preset cover.
function monogramStyle(title) {
  let hash = 0;
  for (const char of title) hash = (hash * 31 + char.codePointAt(0)) % 360;
  return `background:linear-gradient(135deg, hsl(${hash} 55% 38%), hsl(${(hash + 50) % 360} 60% 26%))`;
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function visibleLinks() {
  const term = searchTerm.toLowerCase();
  return links.filter((link) => {
    if (activeCategory && link.category !== activeCategory) return false;
    if (!term) return true;
    return [link.title, link.description, link.category, link.url]
      .join(" ")
      .toLowerCase()
      .includes(term);
  });
}

function render() {
  const items = visibleLinks();
  const container = grid();
  container.innerHTML = "";

  document.getElementById("links-empty").hidden = links.length > 0;
  document.getElementById("links-count").textContent =
    links.length === 0
      ? ""
      : `${items.length} of ${links.length} shortcut${links.length === 1 ? "" : "s"}`;

  items.forEach((link, index) => {
    const card = document.createElement("a");
    card.className = "card";
    card.href = link.url;
    card.target = "_blank";
    card.rel = "noopener";
    card.dataset.id = link.id;
    card.draggable = true;
    card.style.setProperty("--i", index);

    const coverBackground = coverStyle(link.accent) || monogramStyle(link.title);
    const monogram = `<div class="card-monogram" style="${coverBackground}">${escapeHtml((link.title[0] || "?").toUpperCase())}</div>`;
    const media = link.image_url
      ? `<img src="${escapeHtml(link.image_url)}" alt="" loading="lazy">`
      : monogram;

    card.innerHTML = `
      <div class="card-media">${media}</div>
      <div class="card-body">
        <h3 class="card-title">${escapeHtml(link.title)}</h3>
        ${link.description ? `<p class="card-desc">${escapeHtml(link.description)}</p>` : ""}
        <div class="card-foot">
          <span class="card-host">${escapeHtml(hostOf(link.url))}</span>
          ${link.category ? `<span class="card-tag">${escapeHtml(link.category)}</span>` : ""}
        </div>
      </div>
      <div class="card-actions">
        <button class="card-action" data-edit title="Edit">✎</button>
        <button class="card-action danger" data-delete title="Remove">✕</button>
      </div>`;

    const image = card.querySelector(".card-media img");
    if (image) {
      image.addEventListener("error", () => {
        card.querySelector(".card-media").innerHTML = monogram;
      });
    }

    card.querySelector("[data-edit]").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openLinkModal(link);
    });
    card.querySelector("[data-delete]").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      confirmDanger(`Remove the shortcut “${link.title}”?`, async () => {
        await api.deleteLink(link.id);
        toast("Shortcut removed", "success");
        await load();
      });
    });

    container.appendChild(card);
  });

  renderChips();
}

function renderChips() {
  const categories = [...new Set(links.map((link) => link.category).filter(Boolean))].sort();
  const container = document.getElementById("category-chips");
  container.innerHTML = "";
  if (categories.length === 0) return;

  const all = document.createElement("button");
  all.className = `chip${activeCategory === "" ? " is-active" : ""}`;
  all.textContent = "all";
  all.addEventListener("click", () => {
    activeCategory = "";
    render();
  });
  container.appendChild(all);

  for (const category of categories) {
    const chip = document.createElement("button");
    chip.className = `chip${activeCategory === category ? " is-active" : ""}`;
    chip.textContent = category;
    chip.addEventListener("click", () => {
      activeCategory = activeCategory === category ? "" : category;
      render();
    });
    container.appendChild(chip);
  }

  const datalist = document.getElementById("category-list");
  datalist.innerHTML = categories.map((c) => `<option value="${escapeHtml(c)}">`).join("");
}

// ----- modal ----------------------------------------------------------

function updatePreview() {
  const url = document.getElementById("image-url-input").value.trim();
  const preview = document.getElementById("image-preview");
  if (url) {
    preview.innerHTML = `<img src="${escapeHtml(url)}" alt="" onerror="this.replaceWith('✕')">`;
  } else if (selectedAccent) {
    preview.innerHTML = `<div class="preview-cover" style="${coverStyle(selectedAccent)}"></div>`;
  } else {
    preview.innerHTML = "<span>∅</span>";
  }
}

function renderCoverSwatches() {
  const container = document.getElementById("cover-presets");
  container.innerHTML = "";
  for (const preset of COVER_PRESETS) {
    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = `cover-swatch${selectedAccent === preset.id ? " is-selected" : ""}`;
    swatch.title = `Cover ${preset.label}`;
    swatch.innerHTML =
      `<span class="cover-swatch-color" style="${coverStyle(preset.id)}"></span>` +
      `<span class="cover-swatch-name">${preset.label}</span>`;
    swatch.addEventListener("click", () => {
      selectedAccent = selectedAccent === preset.id ? "" : preset.id;
      if (selectedAccent) document.getElementById("image-url-input").value = "";
      renderCoverSwatches();
      updatePreview();
    });
    container.appendChild(swatch);
  }
}

function openLinkModal(link = null) {
  editingId = link ? link.id : null;
  selectedAccent = link ? link.accent || "" : "";
  const form = document.getElementById("link-form");
  form.reset();
  document.getElementById("link-modal-title").textContent = link ? "Edit link" : "New link";
  if (link) {
    form.title.value = link.title;
    form.url.value = link.url;
    form.description.value = link.description;
    form.category.value = link.category;
    form.image_url.value = link.image_url;
  }
  renderCoverSwatches();
  updatePreview();
  openModal("link-modal");
}

async function submitLink(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    title: form.title.value.trim(),
    url: form.url.value.trim(),
    description: form.description.value.trim(),
    category: form.category.value.trim(),
    image_url: form.image_url.value.trim(),
    accent: form.image_url.value.trim() ? "" : selectedAccent,
  };
  const button = document.getElementById("link-save-btn");
  button.disabled = true;
  try {
    if (editingId) {
      await api.updateLink(editingId, payload);
      toast("Shortcut updated", "success");
    } else {
      await api.createLink(payload);
      toast("Shortcut created", "success");
    }
    closeModal("link-modal");
    await load();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function load() {
  links = await api.listLinks();
  render();
}

async function persistOrder(ids) {
  const previous = links.map((link) => link.id);
  if (ids.join("|") === previous.join("|")) return;
  const byId = Object.fromEntries(links.map((link) => [link.id, link]));
  links = ids.map((id) => byId[id]).filter(Boolean);
  try {
    await api.reorderLinks(ids);
  } catch (error) {
    toast(`Could not save the order: ${error.message}`, "error");
    await load();
  }
}

export async function initLinks() {
  makeSortable(grid(), {
    itemSelector: ".card",
    axis: "grid",
    // reordering a filtered subset would scramble hidden items
    canDrag: () => !activeCategory && !searchTerm,
    onReorder: persistOrder,
  });

  registerShare({
    type: "link",
    button: document.getElementById("link-share-btn"),
    list: grid(),
    itemSelector: ".card",
    reload: load,
  });

  document.getElementById("link-new-btn").addEventListener("click", () => openLinkModal());
  document.getElementById("link-empty-btn").addEventListener("click", () => openLinkModal());
  document.getElementById("link-form").addEventListener("submit", submitLink);

  document.getElementById("link-search").addEventListener("input", (event) => {
    searchTerm = event.target.value;
    render();
  });

  const urlInput = document.getElementById("image-url-input");
  urlInput.addEventListener("change", () => {
    if (urlInput.value.trim()) selectedAccent = "";
    renderCoverSwatches();
    updatePreview();
  });

  document.getElementById("image-clear-btn").addEventListener("click", () => {
    urlInput.value = "";
    selectedAccent = "";
    renderCoverSwatches();
    updatePreview();
  });

  document.getElementById("image-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const { url } = await api.uploadImage(file);
      urlInput.value = url;
      selectedAccent = "";
      renderCoverSwatches();
      updatePreview();
      toast("Image uploaded", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      event.target.value = "";
    }
  });

  await load();
}
