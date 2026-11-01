// Generic drag-to-reorder for a container of items (HTML5 drag & drop).
// Items must carry data-id. The DOM is reordered live while dragging;
// onReorder receives the final id sequence on drop.

export function makeSortable(
  container,
  { itemSelector, onReorder, axis = "list", canDrag = () => true }
) {
  let dragged = null;

  container.addEventListener("dragstart", (event) => {
    const item = event.target.closest(itemSelector);
    if (!item || !canDrag()) {
      event.preventDefault();
      return;
    }
    dragged = item;
    // .dnd-active disables entrance animations, which would replay on
    // every insertBefore while dragging
    container.classList.add("dnd-active");
    item.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.dataset.id || "");
  });

  container.addEventListener("dragover", (event) => {
    if (!dragged) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const target = event.target.closest(itemSelector);
    if (!target || target === dragged || target.parentElement !== container) return;
    const rect = target.getBoundingClientRect();
    const before =
      axis === "grid"
        ? event.clientX < rect.left + rect.width / 2
        : event.clientY < rect.top + rect.height / 2;
    container.insertBefore(dragged, before ? target : target.nextSibling);
  });

  container.addEventListener("drop", (event) => {
    if (dragged) event.preventDefault();
  });

  container.addEventListener("dragend", () => {
    if (!dragged) return;
    dragged.classList.remove("is-dragging");
    container.classList.remove("dnd-active");
    dragged = null;
    const ids = [...container.querySelectorAll(itemSelector)].map((element) => element.dataset.id);
    onReorder(ids);
  });
}
