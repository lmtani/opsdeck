// Logs view: the log sources and saved filters of the sidebar, the
// background count of new entries of watched filters, query execution, entry rendering, the trace drill-down (every entry that shares a
// trace / operation with one row, optionally across sources) and
// click-to-refine on field values. Refinements travel as {path, value, negate}
// and the backend spells them in the source's query language.

import { api } from "./api.js";
import { makeSortable } from "./dnd.js";
import { registerShare } from "./share.js";
import { hideTooltip, setupTooltips, tooltipContent } from "./tooltip.js";
import {
  closeModal,
  confirmDanger,
  escapeHtml,
  LOCALE,
  messageHtml,
  openModal,
  toast,
} from "./ui.js";

let filters = [];
let sources = [];
let activeFilterId = null;
let lastEntries = [];
let liveTemplate = "";
let editingId = null;
let editingSourceId = null;

// Watched filters: what each one caught since it was last opened, by filter id
// (GET /api/filters/unseen, counted per severity band by the backend). The
// count refreshes every few minutes while the app-wide "live" switch is on.
let unseen = {};
let polling = true;
let lastWatchPoll = 0;
const WATCH_TICK_MS = 60_000;
const WATCH_REFRESH_MS = 2 * 60_000;
const EYE_ICON =
  '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
  '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" fill="none" stroke="currentColor" stroke-width="2"/>' +
  '<circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';

// What the results panel is showing. `mode` is "idle", "filter" (a saved
// filter on its `source`, plus ad-hoc `refinements` stacked on top of it) or
// "trace" (entries correlated with one row, over one or more `sources`). A
// query's result sets `expression` (what ran, refinements included) and
// `consoleUrl`. A trace view keeps `parent` — the previous view plus its
// entries — so "back" needs no query.
let view = { mode: "idle" };

const TRACE_WINDOW_MS = 60 * 60 * 1000; // ±1 h around the origin entry
const TRACE_LIMIT = 300;
const MAX_REFINE_VALUE_LENGTH = 120;
const MAX_DETAIL_CHIPS = 24;
// leaf keys that are unique per request or otherwise useless as a filter
const NOISY_REFINE_KEYS = new Set([
  "startTime",
  "endTime",
  "latency",
  "requestSize",
  "responseSize",
  "megaCycles",
  "traceId",
  "spanId",
  "requestId",
  "instanceId",
  "clone_id",
]);

const FRESHNESS_LABELS = {
  "15m": "15 min",
  "1h": "1 h",
  "6h": "6 h",
  "1d": "24 h",
  "3d": "3 days",
  "7d": "7 days",
};

const FRESHNESS_MS = {
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "6h": 6 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
  "3d": 3 * 24 * 60 * 60_000,
  "7d": 7 * 24 * 60 * 60_000,
};

// How each kind of log source is presented: its form fields and how its
// queries read. The backend validates the config and writes the queries.
const SOURCE_KINDS = {
  gcp: {
    label: "Google Cloud Logging",
    tag: "GCP",
    fields: [{ key: "project", label: "GCP project", placeholder: "my-project-prod" }],
    hint:
      "Read with the Application Default Credentials (gcloud auth application-default login); " +
      "the account needs roles/logging.viewer on the project.",
    syntax: "Cloud Logging syntax",
    placeholder: 'resource.type="cloud_run_revision"\nseverity>=ERROR',
    console: "GCP",
    traces: true, // entries carry trace / operation.id, and the trace view queries them
  },
};

function kindOf(source) {
  return (
    SOURCE_KINDS[source?.kind] || {
      label: source?.kind || "unknown",
      tag: String(source?.kind || "?").toUpperCase(),
      fields: [],
      hint: "",
      syntax: "the source's syntax",
      placeholder: "",
      console: "console",
      traces: false,
    }
  );
}

function sourceById(id) {
  return sources.find((source) => source.id === id) || null;
}

// The place a source reads, from its kind's fields (for GCP, the project).
function sourcePlace(source) {
  return kindOf(source)
    .fields.map((field) => source.config?.[field.key])
    .filter(Boolean)
    .join(" · ");
}

function plural(count, noun, pluralForm = `${noun}s`) {
  return `${count} ${count === 1 ? noun : pluralForm}`;
}

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

// ----- sidebar ---------------------------------------------------------

function renderFilterList() {
  const container = document.getElementById("filter-list");
  container.innerHTML = "";
  document.getElementById("filters-empty").hidden = filters.length > 0;
  // without a source there is nothing a filter could read: lead to one first
  document.getElementById("filters-empty-text").textContent = sources.length
    ? "Save log queries to run them with one click."
    : "Add a log source first: where your logs live, such as a Google Cloud project.";
  document.getElementById("filter-empty-btn").textContent = sources.length
    ? "Create filter"
    : "Add a source";
  document.getElementById("filter-new-btn").disabled = !sources.length;

  filters.forEach((filter, index) => {
    const item = document.createElement("div");
    item.className = `filter-item${filter.id === activeFilterId ? " is-active" : ""}`;
    item.dataset.id = filter.id;
    item.draggable = true;
    item.style.setProperty("--i", index);
    item.setAttribute("role", "button");
    item.tabIndex = 0;
    const source = sourceById(filter.source_id);
    item.innerHTML = `
      <span class="filter-name">${escapeHtml(filter.name)}${watchMarks(filter)}</span>
      <span class="filter-meta">
        <span title="${escapeHtml(source ? `${kindOf(source).label}: ${sourcePlace(source)}` : "")}">${escapeHtml(source?.name || "no source")}</span>
        <span>· ${FRESHNESS_LABELS[filter.freshness] || filter.freshness}</span>
        <span>· ${filter.limit}</span>
      </span>
      <span class="filter-edit">
        <button class="card-action${filter.watch ? " is-on" : ""}" data-watch
          title="${filter.watch ? "Stop watching" : "Watch: count new entries in the background"}">${EYE_ICON}</button>
        <button class="card-action" data-edit title="Edit">✎</button>
        <button class="card-action danger" data-delete title="Remove">✕</button>
      </span>`;

    const run = () => runFilter(filter);
    item.addEventListener("click", run);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        run();
      }
    });
    item.querySelector("[data-watch]").addEventListener("click", (event) => {
      event.stopPropagation();
      toggleWatch(filter);
    });
    item.querySelector("[data-edit]").addEventListener("click", (event) => {
      event.stopPropagation();
      openFilterModal(filter);
    });
    item.querySelector("[data-delete]").addEventListener("click", (event) => {
      event.stopPropagation();
      confirmDanger(`Remove the filter “${filter.name}”?`, async () => {
        await api.deleteFilter(filter.id);
        if (activeFilterId === filter.id) {
          activeFilterId = null;
          view = { mode: "idle" };
          lastEntries = [];
          document.getElementById("results").hidden = true;
          document.getElementById("results-idle").hidden = false;
        }
        toast("Filter removed", "success");
        await loadFilters();
      });
    });

    container.appendChild(item);
  });
  renderTabBadge();
}

// ----- watched filters -------------------------------------------------

const compactCount = (count) => (count >= 1000 ? `${Math.floor(count / 1000)}k` : String(count));

// The eye of a watched filter plus one chip per severity band that caught
// something since the filter was last opened ("+" when a read hit its cap).
function watchMarks(filter) {
  if (!filter.watch) return "";
  const eye = polling
    ? `<span class="log-watching" title="Watching: new entries are counted every few minutes">${EYE_ICON}</span>`
    : `<span class="log-watching is-paused" title="Watching, waiting for live updates to resume">${EYE_ICON}</span>`;
  const status = unseen[filter.id];
  if (!status) return eye;
  const plus = status.capped ? "+" : "";
  const since = new Date(status.since).toLocaleString(LOCALE, {
    hourCycle: "h23",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const chips = SEVERITY_BANDS.filter(({ band }) => status[band] > 0)
    .map(
      ({ band, label }) =>
        `<span class="log-unseen lsev-${band}" title="${status[band]}${plus} new ${escapeHtml(label)} since ${since}">${compactCount(status[band])}${plus}</span>`
    )
    .join("");
  const problem = status.problem
    ? `<span class="log-unseen is-problem" title="${escapeHtml(`Count failed: ${status.problem}`)}">!</span>`
    : "";
  return eye + chips + problem;
}

// The Logs tab shows the new entries of every watched filter, colored by the
// most severe band among them, so they are visible from the other tabs.
function renderTabBadge() {
  const badge = document.getElementById("logs-tab-badge");
  const statuses = filters
    .filter((filter) => filter.watch && unseen[filter.id])
    .map((f) => unseen[f.id]);
  const sum = (key) => statuses.reduce((total, status) => total + status[key], 0);
  const total = sum("total");
  const plus = statuses.some((status) => status.capped && status.total) ? "+" : "";
  badge.hidden = total === 0;
  badge.textContent = total > 99 ? "99+" : `${total}${plus}`;
  badge.title = `${total}${plus} new log entries in watched filters`;
  badge.classList.toggle("is-warn", sum("error") === 0 && sum("warning") > 0);
  badge.classList.toggle("is-calm", sum("error") === 0 && sum("warning") === 0);
}

async function refreshUnseen() {
  lastWatchPoll = Date.now();
  if (!filters.some((filter) => filter.watch)) {
    unseen = {};
    renderTabBadge();
    return;
  }
  try {
    const statuses = await api.logsUnseen();
    unseen = Object.fromEntries(statuses.map((status) => [status.filter_id, status]));
    renderFilterList();
  } catch {
    /* the counts are best effort: try again on the next round */
  }
}

function watchTick() {
  if (!polling || document.hidden) return;
  if (Date.now() - lastWatchPoll < WATCH_REFRESH_MS) return;
  refreshUnseen();
}

function replaceFilter(updated) {
  filters = filters.map((filter) => (filter.id === updated.id ? updated : filter));
  if (view.filter?.id === updated.id) view.filter = updated;
}

async function toggleWatch(filter) {
  try {
    const updated = await api.watchFilter(filter.id, !filter.watch);
    replaceFilter(updated);
    if (!updated.watch) delete unseen[updated.id];
    renderFilterList();
    const when = polling ? "every few minutes" : "once live updates resume";
    toast(
      updated.watch
        ? `Watching “${updated.name}”: new entries are counted ${when}.`
        : `Stopped watching “${updated.name}”.`,
      updated.watch ? "success" : "info"
    );
    if (updated.watch) refreshUnseen();
  } catch (error) {
    toast(error.message, "error");
  }
}

// Opening a watched filter is seeing what it caught: its count starts over.
async function markSeen(filterId) {
  try {
    replaceFilter(await api.markFilterSeen(filterId));
    delete unseen[filterId];
    renderFilterList();
  } catch {
    /* the chips clear on the next round */
  }
}

function renderSourceList() {
  const container = document.getElementById("source-list");
  container.innerHTML = sources.length
    ? ""
    : '<p class="sources-empty">None yet. A source is where logs are read from.</p>';
  for (const source of sources) {
    const kind = kindOf(source);
    const place = sourcePlace(source);
    const item = document.createElement("div");
    item.className = "source-item";
    item.title = `${source.name}\n${kind.label}: ${place}`;
    // a source named after its project (as migrated ones are) shows it once
    item.innerHTML = `
      <span class="source-kind">${escapeHtml(kind.tag)}</span>
      <span class="source-name">${escapeHtml(source.name)}</span>
      ${place === source.name ? "" : `<span class="source-place">${escapeHtml(place)}</span>`}
      <span class="filter-edit">
        <button class="card-action" data-edit title="Edit" aria-label="Edit ${escapeHtml(source.name)}">✎</button>
        <button class="card-action danger" data-delete title="Remove" aria-label="Remove ${escapeHtml(source.name)}">✕</button>
      </span>`;
    item.querySelector("[data-edit]").addEventListener("click", () => openSourceModal(source));
    item.querySelector("[data-delete]").addEventListener("click", () => {
      confirmDanger(`Remove the log source “${source.name}”?`, async () => {
        try {
          await api.deleteSource(source.id);
          toast("Source removed", "success");
          await loadSources();
        } catch (error) {
          toast(error.message, "error");
        }
      });
    });
    container.appendChild(item);
  }
}

// ----- line formatting -------------------------------------------------
// A template like "{httpRequest.status} {resource.type} → {labels.foo}"
// renders each line from any field in the raw GCP record using dot paths
// (httpRequest, labels, resource, trace, operation, textPayload, …).
// Normalized aliases (severity, timestamp, resource_type, summary, …) are
// also available; payload keys win on conflict.

function templateContext(entry) {
  const raw = entry.fields && typeof entry.fields === "object" ? entry.fields : {};
  const base = {
    ...raw,
    severity: entry.severity,
    timestamp: entry.timestamp,
    resource_type: entry.resource_type,
    log_name: entry.log_name,
    insert_id: entry.insert_id,
    summary: entry.summary,
    payload: entry.payload,
  };
  if (entry.payload && typeof entry.payload === "object") {
    return { ...base, ...entry.payload };
  }
  return base;
}

function resolvePath(entry, path) {
  let value = templateContext(entry);
  for (const key of path.trim().split(".")) {
    if (value === null || value === undefined) return undefined;
    value = value[key];
  }
  return value;
}

function renderLine(template, entry) {
  let placeholders = 0;
  let resolved = 0;
  const text = template.replace(/\{([^{}]+)\}/g, (_, path) => {
    placeholders += 1;
    const value = resolvePath(entry, path);
    if (value === undefined || value === null) return "—";
    resolved += 1;
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  });
  return { text, placeholders, resolved };
}

// The saved template is written for one shape of record (e.g. the request
// log). Rows of another shape — app logs inside a trace — would render as
// "— [—] | 422 — —", so when most placeholders miss the row shows its summary.
function lineFor(entry) {
  if (!liveTemplate) return entry.summary;
  const { text, placeholders, resolved } = renderLine(liveTemplate, entry);
  return placeholders > 0 && resolved * 2 < placeholders ? entry.summary : text;
}

// Walk a record into dot-path leaves (httpRequest.status, resource.labels.zone,
// …), recursing through plain objects up to a depth/count budget.
function collectPaths(value, prefix, out, depth) {
  if (out.size >= 60) return;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if (depth >= 3) {
      if (prefix) out.add(prefix);
      return;
    }
    for (const [key, sub] of Object.entries(value)) {
      collectPaths(sub, prefix ? `${prefix}.${key}` : key, out, depth + 1);
    }
  } else if (prefix) {
    out.add(prefix);
  }
}

function detectFields(entries) {
  const fields = new Set();
  for (const entry of entries.slice(0, 5)) {
    const raw =
      entry.fields && typeof entry.fields === "object" && Object.keys(entry.fields).length
        ? entry.fields
        : entry.payload && typeof entry.payload === "object"
          ? entry.payload
          : null;
    if (raw) collectPaths(raw, "", fields, 0);
  }
  return [...fields].sort().slice(0, 50);
}

function renderFieldChips() {
  const container = document.getElementById("format-fields");
  const fields = detectFields(lastEntries);
  container.innerHTML = fields.length
    ? '<span class="format-fields-label">detected fields</span>'
    : '<span class="format-fields-label">no structured payload in this query</span>';
  for (const field of fields) {
    const chip = document.createElement("button");
    chip.className = "field-chip";
    chip.textContent = `{${field}}`;
    chip.title = "Insert into the format";
    chip.addEventListener("click", () => {
      const input = document.getElementById("format-input");
      const needsSpace = input.value && !input.value.endsWith(" ");
      input.value += `${needsSpace ? " " : ""}{${field}}`;
      liveTemplate = input.value;
      rerenderWithTemplate();
      input.focus();
    });
    container.appendChild(chip);
  }
}

// Re-render preserving error boxes: only when there is something to show.
function rerenderWithTemplate() {
  if (lastEntries.length) paintEntries();
}

// ----- correlation: trace / operation -----------------------------------
// Only for kinds with `traces` (GCP): the entries carry trace / operation.id,
// and the correlated query is written in Cloud Logging syntax here.

// Filter string literal: quotes and backslashes escaped.
function quoteFilterValue(value) {
  return `"${String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function shortId(id) {
  return id.length > 8 ? id.slice(-7) : id;
}

// The key that ties an entry to its siblings: the request trace when there is
// one, otherwise the long-running operation id. Null for standalone lines.
function correlationKey(entry) {
  if (entry.trace) {
    const match = /^projects\/([^/]+)\/traces\/(.+)$/.exec(entry.trace);
    const id = match ? match[2] : entry.trace.split("/").pop();
    return {
      kind: "trace",
      id,
      project: match ? match[1] : "",
      value: entry.trace,
      label: `⌁ ${shortId(id)}`,
      title: `Show every entry of this trace\n${entry.trace}`,
    };
  }
  if (entry.operation_id) {
    return {
      kind: "operation",
      id: entry.operation_id,
      project: "",
      value: entry.operation_id,
      label: `⟳ ${shortId(entry.operation_id)}`,
      title: `Show every entry of this operation\n${entry.operation_id}`,
    };
  }
  return null;
}

// Equality clause for `key` as seen from `project`. A trace path carries its
// own project prefix, which is rewritten when looking in another project.
function correlationExpression(key, project) {
  if (key.kind === "trace") {
    const path = key.project === project ? key.value : `projects/${project}/traces/${key.id}`;
    return `trace=${quoteFilterValue(path)}`;
  }
  return `operation.id=${quoteFilterValue(key.id)}`;
}

// Full query for one project: the equality clause plus, when the origin has a
// timestamp, a window around it (an explicit timestamp clause makes both
// providers ignore the relative freshness).
function correlationQuery(key, project, window) {
  const base = correlationExpression(key, project);
  if (!window) return { base, expression: base };
  return {
    base,
    expression:
      `${base} AND timestamp>=${quoteFilterValue(window.start)}` +
      ` AND timestamp<=${quoteFilterValue(window.end)}`,
  };
}

function windowAround(timestamp) {
  const instant = Date.parse(timestamp);
  if (!Number.isFinite(instant)) return null;
  return {
    start: new Date(instant - TRACE_WINDOW_MS).toISOString(),
    end: new Date(instant + TRACE_WINDOW_MS).toISOString(),
  };
}

function sortAscending(entries) {
  return [...entries].sort((a, b) => {
    const delta = (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0);
    return delta || String(a.timestamp).localeCompare(String(b.timestamp));
  });
}

function formatDelta(ms) {
  if (!Number.isFinite(ms)) return "—";
  const sign = ms < 0 ? "-" : "+";
  const abs = Math.abs(ms);
  if (abs < 1000) return `${sign}${Math.round(abs)} ms`;
  if (abs < 60_000) {
    return `${sign}${(abs / 1000).toFixed(abs < 10_000 ? 2 : 1)} s`;
  }
  const minutes = Math.floor(abs / 60_000);
  const seconds = Math.round((abs % 60_000) / 1000);
  if (minutes < 60) return `${sign}${minutes} min ${seconds} s`;
  return `${sign}${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

// Sources of the trace's kind that it has not searched yet, one per place.
function otherSources() {
  if (view.mode !== "trace") return [];
  const places = new Set(view.sources.map(sourcePlace));
  return sources.filter((source) => {
    if (source.kind !== view.source.kind || places.has(sourcePlace(source))) return false;
    places.add(sourcePlace(source));
    return true;
  });
}

// ----- refinements (filter mode) -----------------------------------------

const fieldLabel = (path) => path.join(".");
const refinementKey = (item) => JSON.stringify([item.path, item.value, item.negate]);
const refinementText = (item) =>
  `${fieldLabel(item.path)} ${item.negate ? "≠" : "="} ${String(item.value)}`;

function addRefinement(path, value, negate) {
  if (view.mode !== "filter") return;
  const refinement = { path, value, negate };
  if (view.refinements.some((item) => refinementKey(item) === refinementKey(refinement))) {
    toast("That refinement is already applied");
    return;
  }
  view.refinements = [...view.refinements, refinement];
  loadFilter();
}

function removeRefinement(index) {
  if (view.mode !== "filter") return;
  view.refinements = view.refinements.filter((_, i) => i !== index);
  loadFilter();
}

function renderRefineBar() {
  const bar = document.getElementById("refine-bar");
  const list = document.getElementById("refine-chips");
  const refinements = view.mode === "filter" ? view.refinements : [];
  bar.hidden = refinements.length === 0;
  list.innerHTML = "";
  refinements.forEach((item, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `refine-chip${item.negate ? " is-negated" : ""}`;
    chip.title = `${refinementText(item)}\nClick to remove`;
    chip.innerHTML =
      `<span class="refine-field">${escapeHtml(fieldLabel(item.path))}</span>` +
      `<span class="refine-op">${item.negate ? "≠" : "="}</span>` +
      `<span class="refine-value">${escapeHtml(truncate(String(item.value), 48))}</span>` +
      `<span class="refine-x">✕</span>`;
    chip.addEventListener("click", () => removeRefinement(index));
    list.appendChild(chip);
  });
}

// Scalar leaves of the raw record that make sense as an equality filter, as
// the path of segments to them (a key may hold dots or slashes, so the path
// is never joined into one string on the way to the backend).
function refineCandidates(entry) {
  const raw = entry.fields && typeof entry.fields === "object" ? entry.fields : {};
  const out = [];
  const push = (path, value) => {
    if (out.length >= MAX_DETAIL_CHIPS) return;
    if (value === null || value === undefined || typeof value === "object") return;
    if (typeof value === "string" && (!value.trim() || value.length > MAX_REFINE_VALUE_LENGTH)) {
      return;
    }
    out.push({ path, value });
  };
  const walk = (object, prefix, depth) => {
    if (!object || typeof object !== "object" || Array.isArray(object)) return;
    for (const [key, value] of Object.entries(object)) {
      if (NOISY_REFINE_KEYS.has(key)) continue;
      const path = [...prefix, key];
      if (value && typeof value === "object" && !Array.isArray(value)) {
        if (depth < 2) walk(value, path, depth + 1);
      } else {
        push(path, value);
      }
    }
  };
  push(["logName"], raw.logName);
  walk(raw.resource?.labels, ["resource", "labels"], 0);
  walk(raw.labels, ["labels"], 0);
  walk(raw.httpRequest, ["httpRequest"], 0);
  walk(raw.operation, ["operation"], 0);
  walk(raw.jsonPayload, ["jsonPayload"], 0);
  walk(raw.protoPayload, ["protoPayload"], 0);
  return out;
}

// ----- results header ----------------------------------------------------

function setHeader(title, meta) {
  view.title = title;
  view.meta = meta;
  document.getElementById("results-title").textContent = title;
  const metaNode = document.getElementById("results-meta");
  metaNode.textContent = meta;
  metaNode.title = "";
  syncChrome();
}

// Buttons and bars that depend on the current view.
function syncChrome() {
  const back = document.getElementById("results-back-btn");
  back.hidden = !view.parent;
  if (view.parent) {
    back.textContent = `← ${view.parent.view.title || "back"}`;
    back.title = "Back (Esc)";
  }
  const others = otherSources();
  const sourcesButton = document.getElementById("trace-sources-btn");
  sourcesButton.hidden = others.length === 0;
  sourcesButton.disabled = false;
  sourcesButton.textContent = `⊕ other sources (${others.length})`;
  sourcesButton.title = `Search the same trace in the other sources:\n${others.map((source) => source.name).join(", ")}`;
  const consoleButton = document.getElementById("open-console-btn");
  consoleButton.hidden = !view.consoleUrl;
  if (view.consoleUrl) {
    const kind = kindOf(view.source);
    consoleButton.textContent = `↗ open in ${kind.console}`;
    consoleButton.title = `Open this query in ${kind.label}`;
  }
  renderRefineBar();
}

function showResultsPanel() {
  document.getElementById("results-idle").hidden = true;
  document.getElementById("results").hidden = false;
}

function showSkeleton() {
  showHistogramSkeleton();
  const entries = document.getElementById("entries");
  entries.innerHTML = Array.from({ length: 7 }, () => '<div class="skeleton"></div>').join("");
}

function showError(error) {
  clearHistogram();
  document.getElementById("entries").innerHTML =
    `<div class="error-box"><strong>Query failed</strong>${messageHtml(error.message)}</div>`;
}

// ----- query execution ---------------------------------------------------

async function runFilter(filter) {
  activeFilterId = filter.id;
  renderFilterList();
  showResultsPanel();

  const source = sourceById(filter.source_id);
  // a watched filter highlights what came after it was last opened
  const newSince = filter.watch ? filter.last_seen_at : null;
  view = { mode: "filter", filter, source, refinements: [], title: filter.name, newSince };
  liveTemplate = filter.line_format || "";
  document.getElementById("format-input").value = liveTemplate;
  await loadFilter();
}

async function loadFilter() {
  const current = view;
  const { filter, refinements } = view;
  const place = view.source?.name || "no source";
  const windowLabel = FRESHNESS_LABELS[filter.freshness] || filter.freshness;
  view.expression = undefined;
  view.consoleUrl = "";
  setHeader(filter.name, `${place} · window ${windowLabel} · querying…`);
  showSkeleton();

  try {
    const result = await api.runFilter(filter.id, refinements);
    if (view !== current) return; // the user moved on while this ran
    view.queriedAt = Date.now();
    view.expression = result.expression;
    view.consoleUrl = result.console_url;
    lastEntries = result.entries;
    paintEntries({ rise: true });
    renderFieldChips();
    if (filters.find((item) => item.id === filter.id)?.watch) markSeen(filter.id);
    const refined = refinements.length ? ` · ${plural(refinements.length, "refinement")}` : "";
    setHeader(
      filter.name,
      `${place} · window ${windowLabel} · ${plural(lastEntries.length, "entry", "entries")}${refined} · ` +
        new Date().toLocaleTimeString(LOCALE, { hourCycle: "h23" })
    );
  } catch (error) {
    if (view !== current) return;
    lastEntries = [];
    setHeader(filter.name, `${place} · failed`);
    showError(error);
  }
}

// Drill down from one row into everything that shares its trace/operation,
// starting in the current source within ±1 h of the row.
async function openTrace(entry) {
  const key = correlationKey(entry);
  if (!key || !view.source || !kindOf(view.source).traces) return;
  const source = view.source;
  const window = windowAround(entry.timestamp);

  view = {
    mode: "trace",
    source,
    sources: [source],
    key,
    origin: entry,
    window,
    parent: { view, entries: lastEntries },
    title: key.kind === "trace" ? `Trace ${shortId(key.id)}` : `Operation ${key.id}`,
  };
  await loadTrace();
}

// Queries every source in view.sources in parallel and merges the results
// into one ascending timeline; each entry is tagged with its source's name.
async function loadTrace() {
  const current = view;
  const { sources: searched, key, window, title } = view;
  const names = searched.map((source) => source.name);
  view.consoleUrl = "";
  setHeader(title, `${names.join(" + ")} · querying…`);
  showSkeleton();

  const results = await Promise.allSettled(
    searched.map((source) =>
      api.queryLogs({
        source_id: source.id,
        expression: correlationQuery(key, source.config.project, window).expression,
        freshness: "1d",
        limit: TRACE_LIMIT,
      })
    )
  );
  if (view !== current) return;

  const entries = [];
  const empty = [];
  const failed = [];
  results.forEach((result, index) => {
    if (result.status === "rejected") {
      failed.push(`${names[index]}: ${result.reason.message}`);
    } else if (result.value.entries.length === 0) {
      empty.push(names[index]);
    } else {
      entries.push(...result.value.entries.map((item) => ({ ...item, source_name: names[index] })));
    }
  });
  // the console opens the trace where it started
  if (results[0].status === "fulfilled") view.consoleUrl = results[0].value.console_url;

  if (failed.length === searched.length) {
    lastEntries = [];
    setHeader(title, `${names.join(" + ")} · failed`);
    showError(new Error(failed.join("\n")));
    return;
  }

  lastEntries = sortAscending(entries);
  paintEntries({ rise: true });
  renderFieldChips();
  const hits = [...new Set(lastEntries.map((item) => item.source_name))];
  const resources = new Set(lastEntries.map((item) => item.resource_type)).size;
  const missing =
    empty.length === 0
      ? ""
      : empty.length > 2
        ? ` · no entries in ${empty.length} other sources`
        : ` · no entries in ${empty.join(", ")}`;
  setHeader(
    title,
    `${hits.join(" + ") || names[0]} · ${plural(lastEntries.length, "entry", "entries")} · ` +
      `${plural(resources, "resource")} · ${window ? "window ±1 h" : "window 24 h"}${missing}`
  );
  document.getElementById("results-meta").title = empty.length
    ? `No entries in: ${empty.join(", ")}`
    : "";
  for (const message of failed) toast(message, "error");
}

async function expandTraceSources() {
  const targets = otherSources();
  if (!targets.length) return;
  const button = document.getElementById("trace-sources-btn");
  button.disabled = true;
  button.textContent = "⊕ searching…";
  view.sources = [...view.sources, ...targets];
  await loadTrace();
}

function goBack() {
  if (!view.parent) return;
  const { view: previous, entries } = view.parent;
  view = previous;
  lastEntries = entries;
  setHeader(view.title, view.meta);
  paintEntries();
  renderFieldChips();
}

function rerun() {
  if (view.mode === "trace") {
    loadTrace();
  } else if (view.mode === "filter") {
    const filter = filters.find((item) => item.id === view.filter.id) || view.filter;
    view.filter = filter;
    view.source = sourceById(filter.source_id) || view.source;
    loadFilter();
  }
}

function openInConsole() {
  if (view.consoleUrl) window.open(view.consoleUrl, "_blank", "noopener");
}

// ----- severity histogram ------------------------------------------------

const HIST_BINS = 60;
const HIST_MIN_SPAN_MS = 1000;
// severities folded into the four bands the bars are stacked by, from the
// bottom up; the tooltip still lists each severity on its own
const SEVERITY_BANDS = [
  { band: "error", label: "error", severities: ["EMERGENCY", "ALERT", "CRITICAL", "ERROR"] },
  { band: "warning", label: "warning", severities: ["WARNING"] },
  { band: "info", label: "info", severities: ["NOTICE", "INFO"] },
  { band: "other", label: "debug / default", severities: ["DEBUG", "DEFAULT"] },
];
const SEVERITY_ORDER = SEVERITY_BANDS.flatMap((item) => item.severities);
const BAND_OF = Object.fromEntries(
  SEVERITY_BANDS.flatMap((item) => item.severities.map((severity) => [severity, item.band]))
);
const bandOf = (severity) => BAND_OF[severity] || "other";

// Placeholder in the histogram's place while a query runs: breathing bars
// with a made-up profile, plus the axis and legend lines so nothing jumps.
function showHistogramSkeleton() {
  hideTooltip();
  const node = document.getElementById("log-hist");
  const bars = Array.from(
    { length: HIST_BINS },
    (_, index) =>
      `<i style="--i:${index};--h:${Math.round(18 + 70 * Math.abs(Math.sin(index * 0.37) * Math.cos(index * 0.11)))}%"></i>`
  ).join("");
  node.innerHTML = `
    <div class="log-hist-skel" role="status" aria-label="Loading entries">${bars}</div>
    <div class="log-hist-axis"><span class="gh-skel log-hist-skel-line"></span></div>
    <div class="log-hist-legend"><span class="gh-skel log-hist-skel-line"></span></div>`;
  node.hidden = false;
}

function clearHistogram() {
  hideTooltip();
  const node = document.getElementById("log-hist");
  node.hidden = true;
  node.innerHTML = "";
}

// The span the bars cover. A filter covers its freshness window up to the
// query time (stretched when an explicit timestamp clause reaches outside it);
// a trace covers its own entries, which usually sit within seconds. The
// provider returns the newest `limit` entries, so a query that hits its limit
// read nothing before its oldest entry: the bars start there (`clipped`)
// instead of drawing that gap as silence.
function histogramRange(times) {
  const oldest = Math.min(...times);
  const newest = Math.max(...times);
  let start = oldest;
  let end = newest;
  let clipped = false;
  if (view.mode === "filter") {
    const queriedAt = view.queriedAt || Date.now();
    const freshness = FRESHNESS_MS[view.filter.freshness];
    end = Math.max(queriedAt, newest);
    clipped = times.length >= view.filter.limit;
    if (freshness && !clipped) start = Math.min(queriedAt - freshness, oldest);
  }
  if (end - start < HIST_MIN_SPAN_MS) {
    const middle = (start + end) / 2;
    start = middle - HIST_MIN_SPAN_MS / 2;
    end = middle + HIST_MIN_SPAN_MS / 2;
  }
  return { start, end, clipped };
}

function histTimeLabel(ms, span) {
  const date = new Date(ms);
  if (span <= 2 * 60_000) {
    return date.toLocaleTimeString(LOCALE, {
      hourCycle: "h23",
      minute: "2-digit",
      second: "2-digit",
    });
  }
  if (span <= 24 * 60 * 60_000) {
    return date.toLocaleTimeString(LOCALE, {
      hourCycle: "h23",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleString(LOCALE, {
    hourCycle: "h23",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function histBinTooltip(bin, label) {
  const severities = SEVERITY_ORDER.filter((severity) => bin.severities.has(severity))
    .concat([...bin.severities.keys()].filter((severity) => !SEVERITY_ORDER.includes(severity)))
    .map(
      (severity) =>
        `<span class="gh-tip-kind lsev-${bandOf(severity)}">${escapeHtml(severity)} ${bin.severities.get(severity)}</span>`
    )
    .join("");
  return (
    `<div class="gh-tooltip-main">${escapeHtml(label)} · ${plural(bin.count, "entry", "entries")}</div>` +
    (severities ? `<div class="gh-tip-kinds">${severities}</div>` : "") +
    (bin.count ? '<div class="gh-tooltip-sub">click to jump to these entries</div>' : "")
  );
}

// Entries per slice of the queried window, stacked by severity band, so the
// errors and warnings stand out against the volume. `rise` grows the columns
// in, for the result of a fresh query (not for a re-render of the same one).
function renderHistogram(entries, { rise = false } = {}) {
  const node = document.getElementById("log-hist");
  const dated = entries
    .map((entry, index) => ({ entry, index, time: Date.parse(entry.timestamp) }))
    .filter((item) => Number.isFinite(item.time));
  if (!dated.length) {
    clearHistogram();
    return;
  }

  const { start, end, clipped } = histogramRange(dated.map((item) => item.time));
  const span = end - start;
  const width = span / HIST_BINS;
  const bins = Array.from({ length: HIST_BINS }, () => ({
    count: 0,
    bands: new Map(),
    severities: new Map(),
    first: null,
  }));
  const totals = new Map();
  for (const { entry, index, time } of dated) {
    const bin = bins[Math.min(HIST_BINS - 1, Math.floor((time - start) / width))];
    const band = bandOf(entry.severity);
    bin.count += 1;
    bin.bands.set(band, (bin.bands.get(band) || 0) + 1);
    bin.severities.set(entry.severity, (bin.severities.get(entry.severity) || 0) + 1);
    if (bin.first === null || index < bin.first) bin.first = index;
    totals.set(band, (totals.get(band) || 0) + 1);
  }
  const peak = Math.max(...bins.map((bin) => bin.count));

  const columns = bins
    .map((bin, index) => {
      const segments = SEVERITY_BANDS.filter(({ band }) => bin.bands.has(band))
        .map(({ band }) => {
          const height = (bin.bands.get(band) / peak) * 100;
          return `<span class="log-hist-seg lsev-${band}" style="height:${height.toFixed(2)}%"></span>`;
        })
        .join("");
      return (
        `<div class="log-hist-col${bin.count ? " has-entries" : ""}" style="--i:${index}" data-tooltip data-hist="${index}">` +
        `<span class="log-hist-stack">${segments}</span></div>`
      );
    })
    .join("");

  const ticks = [0, 0.25, 0.5, 0.75, 1]
    .map((fraction) => `<span>${escapeHtml(histTimeLabel(start + fraction * span, span))}</span>`)
    .join("");
  const legend = SEVERITY_BANDS.filter(({ band }) => totals.has(band))
    .map(
      ({ band, label }) =>
        `<span class="log-hist-key lsev-${band}"><i></i>${totals.get(band)} ${escapeHtml(label)}</span>`
    )
    .join("");
  const clip = clipped
    ? `<span class="log-hist-clip">limit of ${view.filter.limit} entries reached: ` +
      `only since ${escapeHtml(absoluteTime(new Date(start).toISOString()))}</span>`
    : "";

  node.innerHTML = `
    <div class="log-hist-bars${rise ? " is-rising" : ""}">${columns}</div>
    <div class="log-hist-axis">${ticks}</div>
    <div class="log-hist-legend">${legend}${clip}</div>`;
  node.hidden = false;

  node.querySelectorAll("[data-hist]").forEach((column) => {
    const index = Number(column.dataset.hist);
    const from = start + index * width;
    const label = `${histTimeLabel(from, span)}–${histTimeLabel(from + width, span)}`;
    tooltipContent.set(column, () => histBinTooltip(bins[index], label));
    if (bins[index].first !== null) {
      column.addEventListener("click", () => jumpToEntry(bins[index].first));
    }
  });
}

function jumpToEntry(index) {
  const row = document.getElementById("entries").children[index];
  if (!row) return;
  row.scrollIntoView({ block: "center", behavior: "smooth" });
  row.classList.remove("is-flash");
  void row.offsetWidth; // restart the animation when the same row is picked again
  row.classList.add("is-flash");
}

// ----- entry rendering ---------------------------------------------------

function paintEntries({ rise = false } = {}) {
  renderHistogram(lastEntries, { rise });
  if (view.mode === "trace") {
    renderEntries(lastEntries, {
      relative: true,
      originId: view.origin.insert_id,
      currentKey: view.key.value,
      showSource: view.sources.length > 1,
    });
  } else {
    renderEntries(lastEntries, { refinable: view.mode === "filter", newSince: view.newSince });
  }
}

function absoluteTime(timestamp) {
  if (!timestamp) return "—";
  return new Date(timestamp).toLocaleString(LOCALE, {
    hourCycle: "h23",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function refineAttrs(path, value, enabled) {
  if (!enabled) return "";
  return (
    ` data-refine-path="${escapeHtml(JSON.stringify(path))}" data-refine-value="${escapeHtml(value)}"` +
    ` title="${escapeHtml(`Filter by ${fieldLabel(path)} = ${value}\nshift+click excludes`)}"`
  );
}

function renderEntries(entries, options = {}) {
  const {
    relative = false,
    originId = null,
    currentKey = null,
    showSource = false,
    refinable = false,
    newSince = null,
  } = options;
  const container = document.getElementById("entries");
  container.innerHTML = "";

  if (entries.length === 0) {
    container.innerHTML =
      '<div class="empty empty-sm"><p>No entries in the selected window.</p></div>';
    return;
  }

  const baseline = relative ? Date.parse(entries[0].timestamp) : NaN;
  const newAfter = newSince ? Date.parse(newSince) : Infinity;

  entries.forEach((entry, index) => {
    const node = document.createElement("div");
    const isOrigin = Boolean(originId) && entry.insert_id === originId;
    const isNew = Date.parse(entry.timestamp) > newAfter;
    node.className = `entry b-${entry.severity}${isOrigin ? " is-origin" : ""}${isNew ? " is-new" : ""}`;
    node.style.setProperty("--i", Math.min(index, 20));

    const absolute = absoluteTime(entry.timestamp);
    const time = relative ? formatDelta(Date.parse(entry.timestamp) - baseline) : absolute;

    const key = correlationKey(entry);
    let chip = "";
    if (key && key.value === currentKey) {
      chip = `<span class="entry-trace is-current" title="${escapeHtml(key.value)}">${escapeHtml(key.label)}</span>`;
    } else if (key) {
      chip = `<button type="button" class="entry-trace" data-trace title="${escapeHtml(key.title)}">${escapeHtml(key.label)}</button>`;
    }
    const sourceTag =
      showSource && entry.source_name
        ? `<span class="entry-project">${escapeHtml(entry.source_name)}</span>`
        : "";
    const refinableClass = refinable ? " is-refinable" : "";

    const summary = lineFor(entry);
    node.innerHTML = `
      <div class="entry-row">
        <span class="entry-time${relative ? " is-relative" : ""}" title="${escapeHtml(absolute)}">${escapeHtml(time)}</span>
        <span class="entry-sev sev-${escapeHtml(entry.severity)}${refinableClass}"${refineAttrs(["severity"], entry.severity, refinable)}>${escapeHtml(entry.severity)}</span>
        <span class="entry-summary" title="${escapeHtml(summary)}">${escapeHtml(summary)}</span>
        ${chip}
        ${sourceTag}
        <span class="entry-resource${refinableClass}"${refineAttrs(["resource", "type"], entry.resource_type, refinable && entry.resource_type)}>${escapeHtml(entry.resource_type)}</span>
      </div>`;

    const traceButton = node.querySelector("[data-trace]");
    if (traceButton) {
      traceButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openTrace(entry);
      });
    }

    node.querySelector(".entry-row").addEventListener("click", (event) => {
      const target = event.target.closest("[data-refine-path]");
      if (target) {
        const path = JSON.parse(target.dataset.refinePath);
        addRefinement(path, target.dataset.refineValue, event.shiftKey);
        return;
      }
      const open = node.querySelector(".entry-detail");
      if (open) {
        open.remove();
        return;
      }
      node.appendChild(buildDetail(entry, refinable));
    });

    container.appendChild(node);
  });
}

function buildDetail(entry, refinable) {
  const detail = document.createElement("div");
  detail.className = "entry-detail";

  const candidates = refinable ? refineCandidates(entry) : [];
  if (candidates.length) {
    const strip = document.createElement("div");
    strip.className = "detail-fields";
    strip.innerHTML = '<span class="format-fields-label">filter by</span>';
    for (const { path, value } of candidates) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "detail-chip";
      chip.title = `${refinementText({ path, value })}\nClick filters · shift+click excludes`;
      chip.innerHTML =
        `<span class="detail-chip-field">${escapeHtml(fieldLabel(path))}</span>` +
        `<span class="detail-chip-value">${escapeHtml(truncate(String(value), 48))}</span>`;
      chip.addEventListener("click", (event) => {
        event.stopPropagation();
        addRefinement(path, value, event.shiftKey);
      });
      strip.appendChild(chip);
    }
    detail.appendChild(strip);
  }

  const payload =
    typeof entry.payload === "string" ? entry.payload : JSON.stringify(entry.payload, null, 2);
  const labels = Object.entries(entry.resource_labels || {})
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");
  const correlation = [
    entry.trace && `trace=${entry.trace}`,
    entry.span_id && `spanId=${entry.span_id}`,
    entry.operation_id && `operation.id=${entry.operation_id}`,
  ]
    .filter(Boolean)
    .join(" ");
  const body = document.createElement("pre");
  body.className = "entry-detail-body";
  body.innerHTML =
    `<span class="entry-detail-meta">${escapeHtml(entry.log_name)}` +
    `${entry.insert_id ? ` · ${escapeHtml(entry.insert_id)}` : ""}` +
    `${labels ? `\n${escapeHtml(labels)}` : ""}` +
    `${correlation ? `\n${escapeHtml(correlation)}` : ""}</span>` +
    escapeHtml(payload ?? "(no payload)");
  detail.appendChild(body);
  return detail;
}

// ----- filter modal ----------------------------------------------------

// `asNew` prefills the form from `filter` but saves a new record (used to
// turn a refined query into its own saved filter).
function openFilterModal(filter = null, { asNew = false } = {}) {
  if (!sources.length) {
    openSourceModal();
    return;
  }
  editingId = filter && !asNew ? filter.id : null;
  const form = document.getElementById("filter-form");
  form.reset();
  document.getElementById("filter-modal-title").textContent = editingId
    ? "Edit filter"
    : "New filter";
  form.source_id.innerHTML = sources
    .map(
      (source) =>
        `<option value="${escapeHtml(source.id)}">${escapeHtml(source.name)} · ${escapeHtml(sourcePlace(source))}</option>`
    )
    .join("");
  // a new filter starts on the source being looked at
  form.source_id.value = filter?.source_id || view.source?.id || sources[0].id;
  syncExpressionField();
  if (filter) {
    form.name.value = filter.name;
    form.expression.value = filter.expression;
    form.freshness.value = filter.freshness;
    form.limit.value = filter.limit;
    form.description.value = filter.description;
    form.line_format.value = filter.line_format || "";
  }
  openModal("filter-modal");
}

// The expression field speaks the chosen source's language.
function syncExpressionField() {
  const form = document.getElementById("filter-form");
  const kind = kindOf(sourceById(form.source_id.value));
  document.getElementById("filter-expression-label").textContent = `Expression (${kind.syntax})`;
  form.expression.placeholder = kind.placeholder;
}

function saveRefinedAsFilter() {
  if (view.mode !== "filter" || !view.refinements.length) return;
  if (view.expression === undefined) {
    toast("Run the refined query first: its expression comes from the result");
    return;
  }
  const { filter } = view;
  openFilterModal(
    { ...filter, name: `${filter.name} · refined`, expression: view.expression },
    { asNew: true }
  );
}

async function submitFilter(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    name: form.name.value.trim(),
    source_id: form.source_id.value,
    expression: form.expression.value.trim(),
    freshness: form.freshness.value,
    limit: Number(form.limit.value) || 50,
    description: form.description.value.trim(),
    line_format: form.line_format.value.trim(),
  };
  const button = document.getElementById("filter-save-btn");
  button.disabled = true;
  try {
    if (editingId) {
      await api.updateFilter(editingId, payload);
      toast("Filter updated", "success");
    } else {
      await api.createFilter(payload);
      toast("Filter created", "success");
    }
    closeModal("filter-modal");
    await loadFilters();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadFilters() {
  filters = await api.listFilters();
  renderFilterList();
  syncChrome();
  refreshUnseen();
}

// ----- source modal ------------------------------------------------------

function renderSourceFields(kindName, config = {}) {
  const kind = kindOf({ kind: kindName });
  document.getElementById("source-config").innerHTML = kind.fields
    .map(
      (field) => `
        <label class="field">
          <span>${escapeHtml(field.label)} *</span>
          <input data-config="${escapeHtml(field.key)}" required maxlength="120" class="mono"
            spellcheck="false" placeholder="${escapeHtml(field.placeholder)}"
            value="${escapeHtml(config[field.key] || "")}" />
        </label>`
    )
    .join("");
  document.getElementById("source-hint").textContent = kind.hint;
}

function openSourceModal(source = null) {
  editingSourceId = source?.id || null;
  const form = document.getElementById("source-form");
  form.reset();
  document.getElementById("source-modal-title").textContent = source
    ? "Edit log source"
    : "New log source";
  form.kind.innerHTML = Object.entries(SOURCE_KINDS)
    .map(([key, kind]) => `<option value="${key}">${escapeHtml(kind.label)}</option>`)
    .join("");
  form.kind.value = source?.kind || "gcp";
  form.kind.disabled = Boolean(source); // the filters on it are written in its kind's language
  form.name.value = source?.name || "";
  form.name.dataset.touched = source ? "1" : "";
  renderSourceFields(form.kind.value, source?.config);
  openModal("source-modal");
}

async function submitSource(event) {
  event.preventDefault();
  const form = event.target;
  const config = Object.fromEntries(
    [...form.querySelectorAll("[data-config]")].map((input) => [
      input.dataset.config,
      input.value.trim(),
    ])
  );
  const name = form.name.value.trim();
  const button = document.getElementById("source-save-btn");
  button.disabled = true;
  try {
    if (editingSourceId) {
      await api.updateSource(editingSourceId, { name, config });
      toast("Source updated", "success");
    } else {
      await api.createSource({ name, kind: form.kind.value, config });
      toast("Source added", "success");
    }
    closeModal("source-modal");
    await loadSources();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadSources() {
  sources = await api.listSources();
  renderSourceList();
  renderFilterList(); // the filters show their source's name
  if (view.source) view.source = sourceById(view.source.id) || view.source;
  document.dispatchEvent(new CustomEvent("logs:sources"));
}

async function persistFilterOrder(ids) {
  const previous = filters.map((filter) => filter.id);
  if (ids.join("|") === previous.join("|")) return;
  const byId = Object.fromEntries(filters.map((filter) => [filter.id, filter]));
  filters = ids.map((id) => byId[id]).filter(Boolean);
  try {
    await api.reorderFilters(ids);
  } catch (error) {
    toast(`Could not save the order: ${error.message}`, "error");
    await loadFilters();
  }
}

export async function initLogs() {
  setupTooltips(document.getElementById("log-hist"));
  makeSortable(document.getElementById("filter-list"), {
    itemSelector: ".filter-item",
    axis: "list",
    onReorder: persistFilterOrder,
  });

  registerShare({
    type: "filter",
    button: document.getElementById("filter-share-btn"),
    list: document.getElementById("filter-list"),
    itemSelector: ".filter-item",
    reload: async () => {
      await loadSources(); // an import may bring sources along
      await loadFilters();
    },
  });

  document.getElementById("filter-new-btn").addEventListener("click", () => openFilterModal());
  document.getElementById("filter-empty-btn").addEventListener("click", () => openFilterModal());
  document.getElementById("filter-form").addEventListener("submit", submitFilter);
  document
    .querySelector("#filter-form [name=source_id]")
    .addEventListener("change", syncExpressionField);

  document.getElementById("source-new-btn").addEventListener("click", () => openSourceModal());
  const sourceForm = document.getElementById("source-form");
  sourceForm.addEventListener("submit", submitSource);
  sourceForm.kind.addEventListener("change", () => renderSourceFields(sourceForm.kind.value));
  // a new source is named after what it reads until the name is typed
  sourceForm.name.addEventListener("input", () => {
    sourceForm.name.dataset.touched = "1";
  });
  document.getElementById("source-config").addEventListener("input", (event) => {
    if (sourceForm.name.dataset.touched || !event.target.matches("[data-config]")) return;
    sourceForm.name.value = event.target.value.trim();
  });

  document.getElementById("rerun-btn").addEventListener("click", rerun);
  document.getElementById("open-console-btn").addEventListener("click", openInConsole);
  document.getElementById("results-back-btn").addEventListener("click", goBack);
  document.getElementById("trace-sources-btn").addEventListener("click", expandTraceSources);

  document.getElementById("refine-clear-btn").addEventListener("click", () => {
    if (view.mode !== "filter") return;
    view.refinements = [];
    loadFilter();
  });
  document.getElementById("refine-save-btn").addEventListener("click", saveRefinedAsFilter);

  // Esc leaves the trace view. Capture phase so it runs before the shared
  // modal-dismiss handler, and steps aside when a modal is the one open.
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape" || !view.parent) return;
      if (document.getElementById("view-logs").hidden) return;
      if (document.querySelector(".modal-backdrop:not([hidden])")) return;
      goBack();
    },
    true
  );

  // ----- format bar ----
  document.getElementById("format-toggle").addEventListener("click", () => {
    const bar = document.getElementById("format-bar");
    bar.hidden = !bar.hidden;
    if (!bar.hidden) document.getElementById("format-input").focus();
  });

  document.getElementById("format-input").addEventListener("input", (event) => {
    liveTemplate = event.target.value;
    rerenderWithTemplate();
  });

  document.getElementById("format-clear-btn").addEventListener("click", () => {
    document.getElementById("format-input").value = "";
    liveTemplate = "";
    rerenderWithTemplate();
  });

  document.getElementById("format-save-btn").addEventListener("click", async () => {
    const filter = filters.find((item) => item.id === activeFilterId);
    if (!filter) return;
    try {
      const updated = await api.updateFilter(filter.id, {
        name: filter.name,
        source_id: filter.source_id,
        expression: filter.expression,
        freshness: filter.freshness,
        limit: filter.limit,
        description: filter.description,
        line_format: liveTemplate.trim(),
      });
      Object.assign(filter, updated);
      toast("Format saved to the filter", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  // the "live" switch (GitHub sidebar) pauses the background count too
  document.addEventListener("polling:change", (event) => {
    polling = event.detail;
    renderFilterList();
    if (polling) refreshUnseen();
  });
  try {
    polling = (await api.getSettings()).polling !== false;
  } catch {
    /* settings unavailable: stay live */
  }
  setInterval(watchTick, WATCH_TICK_MS);

  await loadSources();
  await loadFilters();
}
