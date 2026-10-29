// Logs view: saved filter sidebar, query execution, entry rendering, the
// trace drill-down (every entry that shares a trace / operation with one row,
// optionally across projects) and click-to-refine on field values.

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
let activeFilterId = null;
let lastEntries = [];
let liveTemplate = "";
let editingId = null;

// What the results panel is showing. `mode` is "idle", "filter" (a saved
// filter, plus ad-hoc `refinements` stacked on top of it) or "trace" (entries
// correlated with one row, over one or more `projects`). A trace view keeps
// `parent` — the previous view plus its entries — so "voltar" needs no query.
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

// freshness → ISO-8601 duration accepted by the Logs Explorer timeRange param
const FRESHNESS_TIMERANGE = {
  "15m": "PT15M",
  "1h": "PT1H",
  "6h": "PT6H",
  "1d": "P1D",
  "3d": "P3D",
  "7d": "P7D",
};

function gcpLogsUrl(filter) {
  const query = encodeURIComponent(filter.expression || "");
  const timeRange = FRESHNESS_TIMERANGE[filter.freshness] || "PT1H";
  return (
    `https://console.cloud.google.com/logs/query;query=${query};timeRange=${timeRange}` +
    `?project=${encodeURIComponent(filter.project)}`
  );
}

function gcpLogsRangeUrl(project, expression, start, end) {
  const query = encodeURIComponent(expression);
  const timeRange = encodeURIComponent(`${start}/${end}`);
  return (
    `https://console.cloud.google.com/logs/query;query=${query};timeRange=${timeRange}` +
    `?project=${encodeURIComponent(project)}`
  );
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

  filters.forEach((filter, index) => {
    const item = document.createElement("div");
    item.className = `filter-item${filter.id === activeFilterId ? " is-active" : ""}`;
    item.dataset.id = filter.id;
    item.draggable = true;
    item.style.setProperty("--i", index);
    item.setAttribute("role", "button");
    item.tabIndex = 0;
    item.innerHTML = `
      <span class="filter-name">${escapeHtml(filter.name)}</span>
      <span class="filter-meta">
        <span>${escapeHtml(filter.project)}</span>
        <span>· ${FRESHNESS_LABELS[filter.freshness] || filter.freshness}</span>
        <span>· ${filter.limit}</span>
      </span>
      <span class="filter-edit">
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

// ----- Cloud Logging filter helpers --------------------------------------

// Filter string literal: quotes and backslashes escaped.
function quoteFilterValue(value) {
  return `"${String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function filterLiteral(value) {
  return typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : quoteFilterValue(value);
}

// ----- correlation: trace / operation -----------------------------------

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

// Projects known from the saved filters that the trace view has not searched.
function otherProjects() {
  if (view.mode !== "trace") return [];
  const known = new Set(filters.map((filter) => filter.project).filter(Boolean));
  return [...known].filter((project) => !view.projects.includes(project)).sort();
}

// ----- refinements (filter mode) -----------------------------------------

function refinementClause(field, value, negate) {
  return `${field}${negate ? "!=" : "="}${filterLiteral(value)}`;
}

function effectiveExpression(filter, refinements) {
  const base = (filter.expression || "").trim();
  if (!refinements.length) return base;
  const clauses = refinements.map((item) => item.clause).join(" AND ");
  return base ? `(${base}) AND ${clauses}` : clauses;
}

function addRefinement(field, value, negate) {
  if (view.mode !== "filter") return;
  const clause = refinementClause(field, value, negate);
  if (view.refinements.some((item) => item.clause === clause)) {
    toast("That refinement is already applied");
    return;
  }
  view.refinements = [...view.refinements, { field, value, negate, clause }];
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
    chip.title = `${item.clause}\nClick to remove`;
    chip.innerHTML =
      `<span class="refine-field">${escapeHtml(item.field)}</span>` +
      `<span class="refine-op">${item.negate ? "≠" : "="}</span>` +
      `<span class="refine-value">${escapeHtml(truncate(String(item.value), 48))}</span>` +
      `<span class="refine-x">✕</span>`;
    chip.addEventListener("click", () => removeRefinement(index));
    list.appendChild(chip);
  });
}

// Scalar leaves of the raw record that make sense as an equality filter,
// grouped by the Cloud Logging field they map to.
function refineCandidates(entry) {
  const raw = entry.fields && typeof entry.fields === "object" ? entry.fields : {};
  const out = [];
  const push = (field, value) => {
    if (out.length >= MAX_DETAIL_CHIPS) return;
    if (value === null || value === undefined || typeof value === "object") return;
    if (typeof value === "string" && (!value.trim() || value.length > MAX_REFINE_VALUE_LENGTH)) {
      return;
    }
    out.push({ field, value });
  };
  const walk = (object, prefix, depth) => {
    if (!object || typeof object !== "object" || Array.isArray(object)) return;
    for (const [key, value] of Object.entries(object)) {
      if (NOISY_REFINE_KEYS.has(key)) continue;
      const field = `${prefix}.${key}`;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        if (depth < 2) walk(value, field, depth + 1);
      } else {
        push(field, value);
      }
    }
  };
  push("logName", raw.logName);
  walk(raw.resource?.labels, "resource.labels", 0);
  walk(raw.labels, "labels", 0);
  walk(raw.httpRequest, "httpRequest", 0);
  walk(raw.operation, "operation", 0);
  walk(raw.jsonPayload, "jsonPayload", 0);
  walk(raw.protoPayload, "protoPayload", 0);
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
  const others = otherProjects();
  const projectsButton = document.getElementById("trace-projects-btn");
  projectsButton.hidden = others.length === 0;
  projectsButton.disabled = false;
  projectsButton.textContent = `⊕ other projects (${others.length})`;
  projectsButton.title = `Search the same trace in the other projects of the saved filters:\n${others.join(", ")}`;
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

  view = { mode: "filter", filter, project: filter.project, refinements: [], title: filter.name };
  liveTemplate = filter.line_format || "";
  document.getElementById("format-input").value = liveTemplate;
  await loadFilter();
}

async function loadFilter() {
  const current = view;
  const { filter, project, refinements } = view;
  const windowLabel = FRESHNESS_LABELS[filter.freshness] || filter.freshness;
  setHeader(filter.name, `${project} · window ${windowLabel} · querying…`);
  showSkeleton();

  // a refined view is an ad-hoc query; the plain saved filter runs by id
  const query = refinements.length
    ? api.queryLogs({
        project,
        expression: effectiveExpression(filter, refinements),
        freshness: filter.freshness,
        limit: filter.limit,
      })
    : api.runFilter(filter.id);

  try {
    const result = await query;
    if (view !== current) return; // the user moved on while this ran
    view.queriedAt = Date.now();
    lastEntries = result.entries;
    paintEntries({ rise: true });
    renderFieldChips();
    const refined = refinements.length ? ` · ${plural(refinements.length, "refinement")}` : "";
    setHeader(
      filter.name,
      `${project} · window ${windowLabel} · ${plural(lastEntries.length, "entry", "entries")}${refined} · ` +
        new Date().toLocaleTimeString(LOCALE, { hourCycle: "h23" })
    );
  } catch (error) {
    if (view !== current) return;
    lastEntries = [];
    setHeader(filter.name, `${project} · failed`);
    showError(error);
  }
}

// Drill down from one row into everything that shares its trace/operation,
// starting in the current project within ±1 h of the row.
async function openTrace(entry) {
  const key = correlationKey(entry);
  if (!key || !view.project) return;
  const project = view.project;
  const window = windowAround(entry.timestamp);

  view = {
    mode: "trace",
    project,
    projects: [project],
    key,
    origin: entry,
    window,
    parent: { view, entries: lastEntries },
    title: key.kind === "trace" ? `Trace ${shortId(key.id)}` : `Operation ${key.id}`,
  };
  await loadTrace();
}

// Queries every project in view.projects in parallel and merges the results
// into one ascending timeline; each entry is tagged with its project.
async function loadTrace() {
  const current = view;
  const { projects, key, window, title } = view;
  setHeader(title, `${projects.join(" + ")} · querying…`);
  showSkeleton();

  const results = await Promise.allSettled(
    projects.map((project) =>
      api
        .queryLogs({
          project,
          expression: correlationQuery(key, project, window).expression,
          freshness: "1d",
          limit: TRACE_LIMIT,
        })
        .then((result) => result.entries.map((item) => ({ ...item, project })))
    )
  );
  if (view !== current) return;

  const entries = [];
  const empty = [];
  const failed = [];
  results.forEach((result, index) => {
    if (result.status === "rejected") {
      failed.push(`${projects[index]}: ${result.reason.message}`);
    } else if (result.value.length === 0) {
      empty.push(projects[index]);
    } else {
      entries.push(...result.value);
    }
  });

  if (failed.length === projects.length) {
    lastEntries = [];
    setHeader(title, `${projects.join(" + ")} · failed`);
    showError(new Error(failed.join("\n")));
    return;
  }

  lastEntries = sortAscending(entries);
  paintEntries({ rise: true });
  renderFieldChips();
  const hits = [...new Set(lastEntries.map((item) => item.project))];
  const resources = new Set(lastEntries.map((item) => item.resource_type)).size;
  const missing =
    empty.length === 0
      ? ""
      : empty.length > 2
        ? ` · no entries in ${empty.length} other projects`
        : ` · no entries in ${empty.join(", ")}`;
  setHeader(
    title,
    `${hits.join(" + ") || projects[0]} · ${plural(lastEntries.length, "entry", "entries")} · ` +
      `${plural(resources, "resource")} · ${window ? "window ±1 h" : "window 24 h"}${missing}`
  );
  document.getElementById("results-meta").title = empty.length
    ? `No entries in: ${empty.join(", ")}`
    : "";
  for (const message of failed) toast(message, "error");
}

async function expandTraceProjects() {
  const targets = otherProjects();
  if (!targets.length) return;
  const button = document.getElementById("trace-projects-btn");
  button.disabled = true;
  button.textContent = "⊕ searching…";
  view.projects = [...view.projects, ...targets];
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
    view.project = filter.project;
    loadFilter();
  }
}

function openInGcp() {
  if (view.mode === "trace") {
    const { base } = correlationQuery(view.key, view.project, view.window);
    const url = view.window
      ? gcpLogsRangeUrl(view.project, base, view.window.start, view.window.end)
      : gcpLogsUrl({ project: view.project, expression: base, freshness: "1d" });
    window.open(url, "_blank", "noopener");
  } else if (view.mode === "filter") {
    const expression = effectiveExpression(view.filter, view.refinements);
    window.open(gcpLogsUrl({ ...view.filter, expression }), "_blank", "noopener");
  }
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
      showProject: view.projects.length > 1,
    });
  } else {
    renderEntries(lastEntries, { refinable: view.mode === "filter" });
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

function refineAttrs(field, value, enabled) {
  if (!enabled) return "";
  return (
    ` data-refine-field="${escapeHtml(field)}" data-refine-value="${escapeHtml(value)}"` +
    ` title="${escapeHtml(`Filter by ${field}=${value}\nshift+click excludes`)}"`
  );
}

function renderEntries(entries, options = {}) {
  const {
    relative = false,
    originId = null,
    currentKey = null,
    showProject = false,
    refinable = false,
  } = options;
  const container = document.getElementById("entries");
  container.innerHTML = "";

  if (entries.length === 0) {
    container.innerHTML =
      '<div class="empty empty-sm"><p>No entries in the selected window.</p></div>';
    return;
  }

  const baseline = relative ? Date.parse(entries[0].timestamp) : NaN;

  entries.forEach((entry, index) => {
    const node = document.createElement("div");
    const isOrigin = Boolean(originId) && entry.insert_id === originId;
    node.className = `entry b-${entry.severity}${isOrigin ? " is-origin" : ""}`;
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
    const projectTag =
      showProject && entry.project
        ? `<span class="entry-project">${escapeHtml(entry.project)}</span>`
        : "";
    const refinableClass = refinable ? " is-refinable" : "";

    const summary = lineFor(entry);
    node.innerHTML = `
      <div class="entry-row">
        <span class="entry-time${relative ? " is-relative" : ""}" title="${escapeHtml(absolute)}">${escapeHtml(time)}</span>
        <span class="entry-sev sev-${escapeHtml(entry.severity)}${refinableClass}"${refineAttrs("severity", entry.severity, refinable)}>${escapeHtml(entry.severity)}</span>
        <span class="entry-summary" title="${escapeHtml(summary)}">${escapeHtml(summary)}</span>
        ${chip}
        ${projectTag}
        <span class="entry-resource${refinableClass}"${refineAttrs("resource.type", entry.resource_type, refinable && entry.resource_type)}>${escapeHtml(entry.resource_type)}</span>
      </div>`;

    const traceButton = node.querySelector("[data-trace]");
    if (traceButton) {
      traceButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openTrace(entry);
      });
    }

    node.querySelector(".entry-row").addEventListener("click", (event) => {
      const target = event.target.closest("[data-refine-field]");
      if (target) {
        addRefinement(target.dataset.refineField, target.dataset.refineValue, event.shiftKey);
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
    for (const { field, value } of candidates) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "detail-chip";
      chip.title = `${refinementClause(field, value, false)}\nClick filters · shift+click excludes`;
      chip.innerHTML =
        `<span class="detail-chip-field">${escapeHtml(field)}</span>` +
        `<span class="detail-chip-value">${escapeHtml(truncate(String(value), 48))}</span>`;
      chip.addEventListener("click", (event) => {
        event.stopPropagation();
        addRefinement(field, value, event.shiftKey);
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
  editingId = filter && !asNew ? filter.id : null;
  const form = document.getElementById("filter-form");
  form.reset();
  document.getElementById("filter-modal-title").textContent = editingId
    ? "Edit filter"
    : "New filter";
  if (filter) {
    form.name.value = filter.name;
    form.project.value = filter.project;
    form.expression.value = filter.expression;
    form.freshness.value = filter.freshness;
    form.limit.value = filter.limit;
    form.description.value = filter.description;
    form.line_format.value = filter.line_format || "";
  }
  openModal("filter-modal");
}

function saveRefinedAsFilter() {
  if (view.mode !== "filter" || !view.refinements.length) return;
  const { filter, refinements } = view;
  openFilterModal(
    {
      ...filter,
      name: `${filter.name} · refined`,
      expression: effectiveExpression(filter, refinements),
    },
    { asNew: true }
  );
}

async function submitFilter(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    name: form.name.value.trim(),
    project: form.project.value.trim(),
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
    reload: loadFilters,
  });

  document.getElementById("filter-new-btn").addEventListener("click", () => openFilterModal());
  document.getElementById("filter-empty-btn").addEventListener("click", () => openFilterModal());
  document.getElementById("filter-form").addEventListener("submit", submitFilter);

  document.getElementById("rerun-btn").addEventListener("click", rerun);
  document.getElementById("open-gcp-btn").addEventListener("click", openInGcp);
  document.getElementById("results-back-btn").addEventListener("click", goBack);
  document.getElementById("trace-projects-btn").addEventListener("click", expandTraceProjects);

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
        project: filter.project,
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

  await loadFilters();
}
