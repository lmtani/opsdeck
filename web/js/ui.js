// Shared UI helpers: modals, toasts, confirm dialog, escaping.

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function openModal(id) {
  const backdrop = document.getElementById(id);
  backdrop.hidden = false;
  const firstInput = backdrop.querySelector("input, textarea, select");
  if (firstInput) setTimeout(() => firstInput.focus(), 60);
}

export function closeModal(id) {
  document.getElementById(id).hidden = true;
}

export function setupModalDismissal() {
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop) backdrop.hidden = true;
    });
    backdrop.querySelectorAll("[data-close]").forEach((button) => {
      button.addEventListener("click", () => (backdrop.hidden = true));
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".modal-backdrop:not([hidden])").forEach((backdrop) => {
      backdrop.hidden = true;
    });
  });
}

export function toast(message, type = "info") {
  const container = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast toast-${type}`;
  node.textContent = message;
  container.appendChild(node);
  setTimeout(() => {
    node.classList.add("is-leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  }, 3200);
}

let confirmHandler = null;

// Shared confirm dialog. Defaults to a destructive "Remover" action; pass
// options to relabel it (e.g. running a command instead of deleting).
export function confirmDanger(text, onConfirm, options = {}) {
  const { title = "Remover?", confirmLabel = "Remover", danger = true } = options;
  document.querySelector("#confirm-modal .modal-title").textContent = title;
  document.getElementById("confirm-text").textContent = text;
  const yes = document.getElementById("confirm-yes");
  yes.textContent = confirmLabel;
  yes.classList.toggle("btn-danger", danger);
  yes.classList.toggle("btn-primary", !danger);
  confirmHandler = onConfirm;
  openModal("confirm-modal");
}

export function setupConfirmModal() {
  document.getElementById("confirm-yes").addEventListener("click", async () => {
    closeModal("confirm-modal");
    if (confirmHandler) await confirmHandler();
    confirmHandler = null;
  });
}
