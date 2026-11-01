// One floating tooltip shared by every view: shown over [data-tip] elements
// whose text is cut off by the ellipsis, and always over elements registered
// in tooltipContent (histogram bins, people, grid cells, graph nodes).

import { escapeHtml } from "./ui.js";

const TOOLTIP_DELAY_MS = 220;
const TOOLTIP_TARGETS =
  "[data-tip], [data-bin], [data-live], [data-person], [data-cell], [data-mix], [data-team], [data-tooltip]";

let tooltip = null;
let tooltipTarget = null;
let tooltipTimer = null;
// elements whose tooltip is HTML built on demand; the builder must escape
// everything it interpolates
export const tooltipContent = new WeakMap();

function isTruncated(element) {
  return element.scrollWidth > element.clientWidth + 1;
}

export function hideTooltip() {
  clearTimeout(tooltipTimer);
  tooltipTarget = null;
  if (tooltip) tooltip.classList.remove("is-visible");
}

function placeTooltip(target) {
  const gap = 8;
  const margin = 12;
  const anchor = target.getBoundingClientRect();
  const box = tooltip.getBoundingClientRect();
  let left = Math.min(anchor.left, window.innerWidth - box.width - margin);
  left = Math.max(margin, left);
  let top = anchor.bottom + gap;
  const above = top + box.height > window.innerHeight - margin;
  if (above) top = anchor.top - box.height - gap;
  tooltip.classList.toggle("is-above", above);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function showTooltip(target) {
  const build = tooltipContent.get(target);
  if (build) {
    tooltip.innerHTML = build();
  } else {
    const main = target.dataset.tip || "";
    const sub = target.dataset.tipSub || "";
    tooltip.innerHTML =
      `<div class="gh-tooltip-main">${escapeHtml(main)}</div>` +
      (sub ? `<div class="gh-tooltip-sub">${escapeHtml(sub)}</div>` : "");
  }
  tooltip.style.left = "0px";
  tooltip.style.top = "0px";
  placeTooltip(target);
  tooltip.classList.add("is-visible");
}

function arm(target) {
  if (target === tooltipTarget) return;
  hideTooltip();
  if (!target) return;
  if (!tooltipContent.has(target) && !isTruncated(target)) return;
  tooltipTarget = target;
  tooltipTimer = setTimeout(() => {
    if (tooltipTarget === target && target.isConnected) showTooltip(target);
  }, TOOLTIP_DELAY_MS);
}

// Creates the tooltip on the first call; every call wires more containers.
export function setupTooltips(...containers) {
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "gh-tooltip";
    tooltip.setAttribute("role", "tooltip");
    document.body.appendChild(tooltip);
    // the anchor moves or disappears: drop the tooltip instead of chasing it
    window.addEventListener("scroll", hideTooltip, { passive: true, capture: true });
    window.addEventListener("resize", hideTooltip);
    document.addEventListener("view:change", hideTooltip);
  }
  for (const container of containers) {
    container.addEventListener("mouseover", (event) => arm(event.target.closest(TOOLTIP_TARGETS)));
    container.addEventListener("mouseleave", hideTooltip);
    // keyboard users get the same tooltips (the mix bar of each person is focusable)
    container.addEventListener("focusin", (event) => arm(event.target.closest(TOOLTIP_TARGETS)));
    container.addEventListener("focusout", hideTooltip);
  }
}
