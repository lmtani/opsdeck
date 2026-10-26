// GitHub view: saved feeds sidebar, org/repo activity timeline grouped by
// day, client-side refinements (repo / kind / author), "new since the last
// visit" marks and the optional 60 s auto-refresh.
//
// Seen marks: the server flags each event of a saved feed as `is_new` when
// it is newer than the feed's last_seen_at, and counts them in `unseen`.
// Leaving the feed (another feed, another tab, closing the page) or
// "✓ visto" moves the mark to the newest event shown.

import { api } from "./api.js";
import { makeSortable } from "./dnd.js";
import {
  announce,
  anyNotifying,
  forgetFeed,
  isNotifying,
  notificationsBlocked,
  toggleNotifications,
} from "./notifications.js";
import { LOCALE, closeModal, confirmDanger, escapeHtml, openModal, toast } from "./ui.js";

const KIND_LABELS = {
  push: "push",
  pr: "PR",
  review: "review",
  comment: "comment",
  issue: "issue",
  branch: "branch/tag",
  release: "release",
  other: "other",
};
const KIND_ORDER = Object.keys(KIND_LABELS);
const STATE_LABELS = { open: "open", draft: "draft", merged: "merged", closed: "closed" };
const FIELD_LABELS = { repo: "repo", kind: "kind", actor: "author" };
const AUTO_REFRESH_MS = 60_000;
const UNSEEN_REFRESH_MS = 5 * 60_000; // badges only, while the page is visible
const NOTIFY_REFRESH_MS = 2 * 60_000; // when some feed notifies, also in the background
const POLL_TICK_MS = 60_000;
const AUTO_KEY = "opsdeck.github.auto";
const GITHUB_WEB = "https://github.com/";
const SUGGESTED_REPOS = 10;

let feeds = [];
let unseen = {}; // feed id -> events newer than its last-seen mark
let available = true;
let visible = false; // the GitHub tab is showing
let editingId = null;
let modalRepos = [];
let modalKinds = new Set();
const repoCache = new Map(); // org -> Promise<repos>

// What the timeline shows. `refinements` is a list of {field, value}: values
// of one field are OR-ed, different fields AND-ed.
let view = { feed: null, activity: null, mark: "", refinements: [], expanded: new Set() };
let requestSeq = 0;
let autoTimer = null;

// ----- small helpers ---------------------------------------------------

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the preference just isn't remembered */
  }
}

const time = (value) => {
  const parsed = Date.parse(value || "");
  return Number.isNaN(parsed) ? 0 : parsed;
};

function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

const shortRepo = (full) => full.split("/").slice(1).join("/") || full;

// only github.com links are rendered as hrefs
const safeUrl = (url) => (String(url || "").startsWith(GITHUB_WEB) ? url : "#");

function avatarUrl(url) {
  return url.includes("?") ? `${url}${url.endsWith("?") ? "" : "&"}s=48` : `${url}?s=48`;
}

function fieldValue(event, field) {
  return field === "repo" ? event.repo : field === "kind" ? event.kind : event.actor;
}

function refinementLabel({ field, value }) {
  if (field === "repo") return shortRepo(value);
  if (field === "kind") return KIND_LABELS[value] || value;
  return value;
}

function matches(event, ignoreField = null) {
  const groups = {};
  for (const { field, value } of view.refinements) {
    if (field !== ignoreField) (groups[field] ||= []).push(value);
  }
  return Object.entries(groups).every(([field, values]) =>
    values.includes(fieldValue(event, field))
  );
}

function feedWebUrl(feed) {
  if (feed.repos.length === 1) return `${GITHUB_WEB}${feed.org}/${feed.repos[0]}`;
  return `${GITHUB_WEB}${feed.org}`;
}

// ----- sidebar ---------------------------------------------------------

function updateTabBadge() {
  const badge = document.getElementById("gh-tab-badge");
  const total = feeds.reduce((sum, feed) => sum + (unseen[feed.id] || 0), 0);
  badge.hidden = total === 0;
  badge.textContent = total > 99 ? "99+" : String(total);
  badge.title = plural(total, "new event", "new events");
}

function renderFeedList() {
  const container = document.getElementById("gh-feed-list");
  container.innerHTML = "";
  document.getElementById("gh-feeds-empty").hidden = feeds.length > 0;

  feeds.forEach((feed, index) => {
    const count = unseen[feed.id] || 0;
    const notifying = isNotifying(feed.id);
    const scope = feed.repos.length ? plural(feed.repos.length, "repo") : "whole org";
    const kinds = feed.event_kinds.length
      ? feed.event_kinds.map((kind) => KIND_LABELS[kind] || kind).join(", ")
      : "all events";

    const item = document.createElement("div");
    item.className = `filter-item${view.feed?.id === feed.id ? " is-active" : ""}`;
    item.dataset.id = feed.id;
    item.draggable = true;
    item.style.setProperty("--i", index);
    item.setAttribute("role", "button");
    item.tabIndex = 0;
    item.innerHTML = `
      <span class="filter-name">${escapeHtml(feed.name)}${
        count
          ? ` <span class="gh-unseen" title="${plural(count, "new event", "new events")}">${
              count > 99 ? "99+" : count
            }</span>`
          : ""
      }${
        notifying ? ' <span class="gh-bell-on" title="Desktop notifications on">🔔</span>' : ""
      }</span>
      <span class="filter-meta">
        <span>${escapeHtml(feed.org)}</span>
        <span>· ${scope}</span>
      </span>
      <span class="filter-meta gh-feed-kinds">${escapeHtml(kinds)}</span>
      <span class="filter-edit">
        <button class="card-action${notifying ? " is-on" : ""}" data-notify
          title="${notifying ? "Stop desktop notifications" : "Notify me of new events"}">${notifying ? "🔕" : "🔔"}</button>
        <button class="card-action" data-edit title="Edit">✎</button>
        <button class="card-action danger" data-delete title="Remove">✕</button>
      </span>`;

    const open = () => openFeed(feed);
    item.addEventListener("click", open);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    item.querySelector("[data-edit]").addEventListener("click", (event) => {
      event.stopPropagation();
      openFeedModal(feed);
    });
    item.querySelector("[data-notify]").addEventListener("click", (event) => {
      event.stopPropagation();
      setNotifications(feed);
    });
    item.querySelector("[data-delete]").addEventListener("click", (event) => {
      event.stopPropagation();
      confirmDanger(`Remove the feed “${feed.name}”?`, () => deleteFeed(feed));
    });
    container.appendChild(item);
  });
  updateTabBadge();
}

async function deleteFeed(feed) {
  try {
    await api.deleteGithubFeed(feed.id);
  } catch (error) {
    toast(`Could not remove: ${error.message}`, "error");
    return;
  }
  feeds = feeds.filter((item) => item.id !== feed.id);
  delete unseen[feed.id];
  forgetFeed(feed.id);
  if (view.feed?.id === feed.id) {
    view = { feed: null, activity: null, mark: "", refinements: [], expanded: new Set() };
    document.getElementById("gh-results").hidden = true;
    document.getElementById("gh-idle").hidden = false;
  }
  renderFeedList();
  toast("Feed removed", "success");
}

async function persistOrder(ids) {
  const byId = new Map(feeds.map((feed) => [feed.id, feed]));
  feeds = ids.map((id) => byId.get(id)).filter(Boolean);
  try {
    await api.reorderGithubFeeds(ids);
  } catch (error) {
    toast(`Could not save the order: ${error.message}`, "error");
  }
}

// ----- seen marks ------------------------------------------------------

function newestEventTime() {
  const events = view.activity?.events || [];
  return events.reduce(
    (newest, event) => (time(event.created_at) > time(newest) ? event.created_at : newest),
    ""
  );
}

// Persist "everything shown up to now was seen" for the active feed.
function markActiveSeen({ keepalive = false } = {}) {
  const feed = view.feed;
  const newest = newestEventTime();
  if (!feed || !newest || time(newest) <= time(feed.last_seen_at)) return false;
  feed.last_seen_at = newest;
  unseen[feed.id] = 0;
  api.markGithubSeen(feed.id, newest, keepalive).catch(() => {});
  renderFeedList();
  return true;
}

async function refreshUnseen() {
  if (!available || feeds.length === 0) return;
  try {
    const counts = await api.githubUnseen();
    // the feed on screen keeps the count of the activity it is showing
    if (visible && view.feed && view.activity) {
      counts[view.feed.id] = view.activity.unseen;
    }
    unseen = counts;
    renderFeedList();
    await notifyNewEvents(counts);
  } catch {
    /* badges and notifications are best effort */
  }
}

// ----- desktop notifications -------------------------------------------

function lookingAt(feed) {
  return visible && view.feed?.id === feed.id && !document.hidden && document.hasFocus();
}

// For each notifying feed with new events that the user is not looking at,
// read its activity and announce what has not been announced yet.
async function notifyNewEvents(counts) {
  for (const feed of feeds) {
    if (!isNotifying(feed.id) || !(counts[feed.id] > 0) || lookingAt(feed)) continue;
    try {
      announce(feed, await api.runFeed(feed.id), openFromNotification);
    } catch {
      /* try again on the next round */
    }
  }
}

function openFromNotification(feed) {
  document.querySelector('.tab[data-view="github"]')?.click();
  openFeed(feeds.find((item) => item.id === feed.id) || feed);
}

async function setNotifications(feed) {
  const outcome = await toggleNotifications(feed.id);
  const messages = {
    on: [`You will be notified of new events in “${feed.name}” while OpsDeck is open in a tab.`, "success"],
    off: [`Notifications for “${feed.name}” are off.`, "info"],
    unsupported: ["This browser cannot show notifications here: they need HTTPS or localhost.", "error"],
    blocked: [
      notificationsBlocked()
        ? "Notifications are blocked for this site. Allow them in the browser's site settings, then try again."
        : "Notifications were not allowed.",
      "error",
    ],
  };
  const [message, kind] = messages[outcome];
  toast(message, kind);
  renderFeedList();
}

// Badges refresh every 5 min while the page is visible; with any feed
// notifying, every 2 min even in a background tab.
let lastPoll = Date.now();

function pollTick() {
  const notifying = anyNotifying(feeds.map((feed) => feed.id));
  if (document.hidden && !notifying) return;
  const every = notifying ? NOTIFY_REFRESH_MS : UNSEEN_REFRESH_MS;
  if (Date.now() - lastPoll < every) return;
  lastPoll = Date.now();
  refreshUnseen();
}

// ----- timeline --------------------------------------------------------

function showSkeleton() {
  document.getElementById("gh-timeline").innerHTML = Array.from(
    { length: 8 },
    () => '<div class="skeleton"></div>'
  ).join("");
}

function openFeed(feed) {
  if (view.feed && view.feed.id !== feed.id) markActiveSeen();
  view = { feed, activity: null, refinements: [], expanded: new Set() };
  renderFeedList();

  document.getElementById("gh-idle").hidden = true;
  document.getElementById("gh-results").hidden = false;
  document.getElementById("gh-title").textContent = feed.name;
  document.getElementById("gh-open-btn").href = feedWebUrl(feed);
  document.getElementById("gh-meta").textContent = `${feed.org} · querying…`;
  document.getElementById("gh-kinds").innerHTML = "";
  document.getElementById("gh-refine").hidden = true;
  document.getElementById("gh-warnings").hidden = true;
  showSkeleton();
  loadActivity({ initial: true });
}

async function loadActivity({ initial = false } = {}) {
  const feed = view.feed;
  if (!feed) return;
  const seq = ++requestSeq;
  const refreshBtn = document.getElementById("gh-refresh-btn");
  refreshBtn.classList.add("is-busy");
  try {
    const activity = await api.runFeed(feed.id);
    if (seq !== requestSeq || view.feed?.id !== feed.id) return;
    view.activity = activity;
    unseen[feed.id] = activity.unseen;
    renderFeedList();
    renderActivity({ animate: initial });
  } catch (error) {
    if (seq !== requestSeq || view.feed?.id !== feed.id) return;
    if (initial || !view.activity) {
      view.activity = null;
      document.getElementById("gh-meta").textContent = `${feed.org} · failed`;
      document.getElementById("gh-timeline").innerHTML =
        `<div class="error-box"><strong>GitHub query failed</strong>${richText(error.message)}</div>`;
    } else {
      toast(`Could not refresh the feed: ${error.message}`, "error");
    }
  } finally {
    if (seq === requestSeq) refreshBtn.classList.remove("is-busy");
  }
}

function renderActivity({ animate = false } = {}) {
  if (!view.activity) return;
  renderKinds();
  renderRefineBar();
  renderWarnings();
  renderTimeline(animate);
  renderMeta();
}

function renderMeta() {
  const { feed, activity } = view;
  const total = activity.events.length;
  const shown = activity.events.filter((event) => matches(event)).length;
  const fresh = activity.unseen;
  const parts = [
    feed.org,
    feed.repos.length ? plural(feed.repos.length, "repo") : "whole org",
    view.refinements.length ? `${shown} of ${plural(total, "event")}` : plural(total, "event"),
  ];
  if (fresh) parts.push(`${fresh} new`);
  if (activity.fetched_at) {
    parts.push(`updated ${new Date(activity.fetched_at).toLocaleTimeString(LOCALE, { hourCycle: "h23" })}`);
  }
  if (activity.rate_remaining != null) {
    parts.push(`quota ${activity.rate_remaining}/${activity.rate_limit}`);
  }
  document.getElementById("gh-meta").textContent = parts.join(" · ");
}

function renderKinds() {
  const container = document.getElementById("gh-kinds");
  const counts = {};
  for (const event of view.activity.events) {
    if (matches(event, "kind")) counts[event.kind] = (counts[event.kind] || 0) + 1;
  }
  const active = new Set(view.refinements.filter((r) => r.field === "kind").map((r) => r.value));
  container.innerHTML = KIND_ORDER.filter((kind) => counts[kind] || active.has(kind))
    .map(
      (kind) => `
        <button class="gh-kind-chip kind-${kind}${active.has(kind) ? " is-active" : ""}"
          data-kind="${kind}" title="Show only ${escapeHtml(KIND_LABELS[kind])}">
          <span>${escapeHtml(KIND_LABELS[kind])}</span><b>${counts[kind] || 0}</b>
        </button>`
    )
    .join("");
  container.querySelectorAll("[data-kind]").forEach((button) => {
    button.addEventListener("click", () => toggleRefinement("kind", button.dataset.kind));
  });
}

function renderRefineBar() {
  const bar = document.getElementById("gh-refine");
  bar.hidden = view.refinements.length === 0;
  const chips = document.getElementById("gh-refine-chips");
  chips.innerHTML = view.refinements
    .map(
      (refinement, index) => `
        <button class="field-chip" data-index="${index}" title="Remove this refinement">
          ${escapeHtml(FIELD_LABELS[refinement.field])}: ${escapeHtml(refinementLabel(refinement))} ✕
        </button>`
    )
    .join("");
  chips.querySelectorAll("[data-index]").forEach((chip) => {
    chip.addEventListener("click", () => {
      view.refinements.splice(Number(chip.dataset.index), 1);
      renderActivity();
    });
  });
  // only repo and kind map onto feed fields
  document.getElementById("gh-refine-save-btn").hidden = !view.refinements.some(
    (r) => r.field !== "actor"
  );
}

function renderWarnings() {
  const box = document.getElementById("gh-warnings");
  const warnings = view.activity.warnings || [];
  box.hidden = warnings.length === 0;
  box.innerHTML = warnings.map((warning) => `<div>⚠ ${richText(warning)}</div>`).join("");
}

function toggleRefinement(field, value) {
  const index = view.refinements.findIndex((r) => r.field === field && r.value === value);
  if (index >= 0) view.refinements.splice(index, 1);
  else view.refinements.push({ field, value });
  renderActivity();
}

function dayLabel(date) {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const formatted = date.toLocaleDateString(LOCALE, {
    weekday: "short",
    day: "2-digit",
    month: "short",
  });
  if (sameDay(date, today)) return ["Today", formatted];
  if (sameDay(date, yesterday)) return ["Yesterday", formatted];
  return [formatted, ""];
}

// ----- per-day volume histogram ---------------------------------------

const BIN_MINUTES = 30;
const BINS_PER_DAY = (24 * 60) / BIN_MINUTES;
const BIN_MS = BIN_MINUTES * 60 * 1000;
const TIP_MAX_ACTORS = 8;

function emptyBin() {
  return { count: 0, actors: new Map(), kinds: new Map() };
}

// Events per local half hour of every day shown, summing `count` so a
// collapsed burst weighs what it really was, with who did what for the
// tooltip. Built from the refined events.
function binnedVolume(events) {
  const days = new Map(); // toDateString() -> bin[48]
  for (const event of events) {
    const date = new Date(event.created_at);
    const key = date.toDateString();
    if (!days.has(key)) days.set(key, Array.from({ length: BINS_PER_DAY }, emptyBin));
    const index = Math.floor((date.getHours() * 60 + date.getMinutes()) / BIN_MINUTES);
    const bin = days.get(key)[index];
    const weight = event.count || 1;
    bin.count += weight;
    const actor = bin.actors.get(event.actor) || { count: 0, avatar: event.actor_avatar };
    actor.count += weight;
    bin.actors.set(event.actor, actor);
    bin.kinds.set(event.kind, (bin.kinds.get(event.kind) || 0) + weight);
  }
  return days;
}

function clockLabel(minutes) {
  const hours = Math.floor(minutes / 60) % 24;
  return `${String(hours).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function binLabel(index) {
  const start = index * BIN_MINUTES;
  return `${clockLabel(start)}–${clockLabel(start + BIN_MINUTES)}`;
}

// Rich tooltip of one half hour: total, contributors and kinds.
function binTooltip(index, bin, note) {
  const actors = [...bin.actors.entries()].sort((a, b) => b[1].count - a[1].count);
  const shown = actors.slice(0, TIP_MAX_ACTORS);
  const rest = actors.length - shown.length;
  const people = shown
    .map(
      ([login, { count, avatar }]) => `
        <div class="gh-tip-actor">
          ${
            avatar
              ? `<img class="gh-tip-avatar" src="${escapeHtml(avatarUrl(avatar))}" alt="" />`
              : '<span class="gh-tip-avatar"></span>'
          }
          <span class="gh-tip-login">${escapeHtml(login)}</span>
          <span class="gh-tip-count">${count}</span>
        </div>`
    )
    .join("");
  const kinds = KIND_ORDER.filter((kind) => bin.kinds.has(kind))
    .map(
      (kind) =>
        `<span class="gh-tip-kind kind-${kind}">${escapeHtml(KIND_LABELS[kind])} ${bin.kinds.get(kind)}</span>`
    )
    .join("");
  return (
    `<div class="gh-tooltip-main">${binLabel(index)} · ${plural(bin.count, "event")}</div>` +
    (note ? `<div class="gh-tooltip-sub">${escapeHtml(note)}</div>` : "") +
    (people ? `<div class="gh-tip-actors">${people}</div>` : "") +
    (rest > 0 ? `<div class="gh-tooltip-sub">+ ${plural(rest, "other contributor", "other contributors")}</div>` : "") +
    (kinds ? `<div class="gh-tip-kinds">${kinds}</div>` : "")
  );
}

// `coverageStart` (ms, or null): before it the feed is incomplete because the
// events API capped a source (activity.coverage_start, computed server-side).
// Those half hours are "sem dados", not silence.
function histogramNode(date, bins, peak, coverageStart) {
  const dayStart = new Date(date);
  dayStart.setHours(0, 0, 0, 0);
  const now = Date.now();
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  const notes = [];

  const columns = bins
    .map((bin, index) => {
      const start = dayStart.getTime() + index * BIN_MS;
      const end = start + BIN_MS;
      let state = "";
      let note = "";
      if (coverageStart !== null && end <= coverageStart) {
        state = " is-nodata";
        note = bin.count
          ? "incomplete data: only out-of-order events arrived"
          : "no data: GitHub only returns the most recent events";
      } else if (start > now) {
        state = " is-future";
        note = "not yet";
      }
      notes[index] = note;
      const height = bin.count && peak ? Math.max(6, Math.round((bin.count / peak) * 100)) : 0;
      return `
        <div class="gh-hist-col${state}${index % 2 ? "" : " is-hour"}" data-bin="${index}">
          <span class="gh-hist-bar" style="height:${height}%"></span>
        </div>`;
    })
    .join("");

  const node = document.createElement("div");
  node.className = "gh-hist";
  node.innerHTML = `
    <div class="gh-hist-bars">${columns}</div>
    <div class="gh-hist-axis">
      <span>0h</span><span>6h</span><span>12h</span><span>18h</span><span>24h</span>
    </div>
    <div class="gh-hist-total">${plural(total, "event")}</div>`;
  node.querySelectorAll("[data-bin]").forEach((column) => {
    const index = Number(column.dataset.bin);
    tooltipContent.set(column, () => binTooltip(index, bins[index], notes[index]));
  });
  return node;
}

function renderTimeline(animate) {
  hideTooltip();
  const container = document.getElementById("gh-timeline");
  container.innerHTML = "";
  const events = view.activity.events.filter((event) => matches(event));

  if (events.length === 0) {
    container.innerHTML = `<div class="empty empty-sm"><p>${
      view.refinements.length
        ? "No events match these refinements."
        : "No recent activity in this feed."
    }</p></div>`;
    return;
  }

  const volume = binnedVolume(events);
  // one scale for every day, so days can be compared at a glance
  const peak = Math.max(...[...volume.values()].flat().map((bin) => bin.count));
  const coverageStart = view.activity.coverage_start ? time(view.activity.coverage_start) : null;

  const anyNew = events.some((event) => event.is_new);
  let lastDay = "";
  let markPlaced = false;
  events.forEach((event, index) => {
    if (anyNew && !markPlaced && !event.is_new) {
      const mark = document.createElement("div");
      mark.className = "gh-new-mark";
      mark.textContent = "▲ new since your last visit";
      container.appendChild(mark);
      markPlaced = true;
    }
    const date = new Date(event.created_at);
    const day = date.toDateString();
    if (day !== lastDay) {
      const [main, sub] = dayLabel(date);
      const header = document.createElement("div");
      header.className = "gh-day";
      header.innerHTML = `${escapeHtml(main)}${sub ? ` <span>${escapeHtml(sub)}</span>` : ""}`;
      container.appendChild(header);
      container.appendChild(histogramNode(date, volume.get(day), peak, coverageStart));
      lastDay = day;
    }
    container.appendChild(eventNode(event, index, animate));
  });
}

// Events read from the PR search, not yet published by the events API.
const isLive = (event) => event.source === "search";

function liveTooltip() {
  return (
    '<div class="gh-tooltip-main">⚡ real time</div>' +
    '<div class="gh-tooltip-sub">From the PR search. GitHub\'s events API has not published ' +
    "this action yet; once it does, its event replaces this one.</div>"
  );
}

function eventNode(event, index, animate) {
  const date = new Date(event.created_at);
  const viewer = (view.activity.viewer || "").toLowerCase();
  const isYou = viewer && event.actor.toLowerCase() === viewer;

  const node = document.createElement("div");
  node.className =
    `gh-event${event.is_new ? " is-new" : ""}${animate ? "" : " no-anim"}`;
  node.style.setProperty("--i", Math.min(index, 20));

  const avatar = event.actor_avatar
    ? `<img class="gh-avatar" src="${escapeHtml(avatarUrl(event.actor_avatar))}" alt="" loading="lazy" />`
    : `<span class="gh-avatar">${escapeHtml(event.actor.slice(0, 1).toUpperCase())}</span>`;
  const title = event.title ? `<span class="gh-title">${escapeHtml(event.title)}</span>` : "";
  const state = event.state
    ? `<span class="gh-state st-${escapeHtml(event.state)}">${escapeHtml(
        STATE_LABELS[event.state] || event.state
      )}</span>`
    : "";

  node.innerHTML = `
    <div class="gh-row">
      <span class="entry-time" title="${escapeHtml(date.toLocaleString(LOCALE, { hourCycle: "h23" }))}">${date.toLocaleTimeString(
        LOCALE,
        { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
      )}</span>
      ${avatar}
      <button class="gh-kind kind-${escapeHtml(event.kind)}" data-refine="kind" title="Refine by kind">${escapeHtml(
        KIND_LABELS[event.kind] || event.kind
      )}</button>
      <span class="gh-text" data-tip="${escapeHtml(`${event.actor} ${event.summary}`)}" data-tip-sub="${escapeHtml(
        event.title
      )}">
        <button class="gh-actor${isYou ? " is-you" : ""}" data-refine="actor" title="Refine by author">${escapeHtml(
          event.actor
        )}</button>
        <span class="gh-summary">${escapeHtml(event.summary)}</span>
        ${title}
      </span>
      ${state}
      ${isLive(event) ? '<span class="gh-live" data-live>⚡</span>' : ""}
      <button class="gh-repo" data-refine="repo" data-tip="${escapeHtml(event.repo)}" data-tip-sub="click to refine by repository">${escapeHtml(
        shortRepo(event.repo)
      )}</button>
      <a class="gh-link" href="${escapeHtml(safeUrl(event.url))}" target="_blank" rel="noopener" title="Open on GitHub">↗</a>
    </div>`;

  node.querySelectorAll("[data-refine]").forEach((button) => {
    button.addEventListener("click", (clickEvent) => {
      clickEvent.stopPropagation();
      toggleRefinement(button.dataset.refine, fieldValue(event, button.dataset.refine));
    });
  });
  node.querySelector(".gh-link").addEventListener("click", (clickEvent) => clickEvent.stopPropagation());
  const live = node.querySelector("[data-live]");
  if (live) tooltipContent.set(live, liveTooltip);
  node.querySelector(".gh-row").addEventListener("click", () => {
    const open = node.querySelector(".gh-detail");
    if (open) {
      open.remove();
      view.expanded.delete(event.id);
      return;
    }
    view.expanded.add(event.id);
    node.appendChild(detailNode(event));
  });
  if (view.expanded.has(event.id)) node.appendChild(detailNode(event));
  return node;
}

function link(url, text) {
  return `<a href="${escapeHtml(safeUrl(url))}" target="_blank" rel="noopener">${escapeHtml(text)}</a>`;
}

function detailNode(event) {
  const repoUrl = `${GITHUB_WEB}${event.repo}`;
  const details = event.details || {};
  const rows = [];
  const row = (label, html) =>
    rows.push(`<span class="gh-detail-key">${escapeHtml(label)}</span><span>${html}</span>`);

  if (event.kind === "push") {
    row("branch", link(`${repoUrl}/tree/${event.ref}`, event.ref));
    const heads = details.heads || [];
    if (heads.length) {
      row("commits", heads.map((sha) => link(`${repoUrl}/commit/${sha}`, sha.slice(0, 7))).join(" "));
    }
    for (const commit of details.commits || []) {
      row(commit.sha, escapeHtml(commit.message));
    }
  } else if (details.head_ref) {
    row("branches", `${escapeHtml(details.head_ref)} → ${escapeHtml(details.base_ref || "?")}`);
  } else if (event.ref) {
    row("ref", escapeHtml(event.ref));
  }
  if (details.path && !details.comments) {
    row("file", escapeHtml(details.line ? `${details.path}:${details.line}` : details.path));
  }
  if (event.number) row("number", link(event.url, `#${event.number}`));
  row("repo", link(repoUrl, event.repo));
  if (isLive(event)) row("source", "PR search, real time · not yet published by the events API");
  row(
    "event",
    `${escapeHtml(event.type)}${event.action ? ` · ${escapeHtml(event.action)}` : ""} · ${escapeHtml(
      new Date(event.created_at).toLocaleString(LOCALE, { hourCycle: "h23" })
    )}`
  );

  // a collapsed burst of code-review comments lists each one
  const comments = (details.comments || [])
    .map(
      (comment) => `
        <div class="gh-comment">
          ${link(comment.url, comment.path || "comment")}
          ${comment.body ? `<div class="gh-body">${escapeHtml(comment.body)}</div>` : ""}
        </div>`
    )
    .join("");

  const detail = document.createElement("div");
  detail.className = "gh-detail";
  detail.innerHTML =
    `<div class="gh-detail-grid">${rows.join("")}</div>` +
    (comments || (event.body ? `<div class="gh-body">${escapeHtml(event.body)}</div>` : ""));
  return detail;
}

// ----- tooltips --------------------------------------------------------

// One floating tooltip for the timeline, shown over [data-tip] elements whose
// text is cut off by the ellipsis, and always over histogram bins, whose
// content comes from the tooltipContent builders.
const TOOLTIP_DELAY_MS = 220;
let tooltip = null;
let tooltipTarget = null;
let tooltipTimer = null;
// elements whose tooltip is HTML built on demand (histogram bins); the
// builder must escape everything it interpolates
const tooltipContent = new WeakMap();

function isTruncated(element) {
  return element.scrollWidth > element.clientWidth + 1;
}

function hideTooltip() {
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

function setupTooltips(container) {
  tooltip = document.createElement("div");
  tooltip.className = "gh-tooltip";
  tooltip.setAttribute("role", "tooltip");
  document.body.appendChild(tooltip);

  container.addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-tip], [data-bin], [data-live]");
    if (target === tooltipTarget) return;
    hideTooltip();
    if (!target) return;
    if (!tooltipContent.has(target) && !isTruncated(target)) return;
    tooltipTarget = target;
    tooltipTimer = setTimeout(() => {
      if (tooltipTarget === target && target.isConnected) showTooltip(target);
    }, TOOLTIP_DELAY_MS);
  });
  container.addEventListener("mouseleave", hideTooltip);
  // the anchor moves or disappears: drop the tooltip instead of chasing it
  window.addEventListener("scroll", hideTooltip, { passive: true, capture: true });
  window.addEventListener("resize", hideTooltip);
  document.addEventListener("view:change", hideTooltip);
}

// ----- auto refresh ----------------------------------------------------

const autoOn = () => storageGet(AUTO_KEY) === "1";

function scheduleAuto() {
  clearInterval(autoTimer);
  autoTimer = null;
  document.getElementById("gh-auto-btn").classList.toggle("is-on", autoOn());
  if (!autoOn()) return;
  autoTimer = setInterval(() => {
    if (visible && !document.hidden && view.feed) loadActivity();
  }, AUTO_REFRESH_MS);
}

// ----- feed modal ------------------------------------------------------

// Repo names are normalized by the server (org prefix and URLs stripped,
// duplicates folded, invalid names rejected): the chips show what it
// answers, and an invalid entry is dropped with its message in the hint.
let previewSeq = 0;

async function addRepos(text) {
  const pieces = text.split(/[\s,]+/).map((piece) => piece.trim()).filter(Boolean);
  if (!pieces.length) return;
  const before = [...modalRepos];
  modalRepos = [...modalRepos, ...pieces];
  renderRepoTags();
  await normalizeRepos(before);
}

async function normalizeRepos(fallback = null) {
  const org = document.getElementById("gh-feed-form").org.value.trim();
  const hint = document.getElementById("gh-repo-hint");
  if (!org || !modalRepos.length) return;
  const seq = ++previewSeq;
  try {
    const spec = await api.previewGithubFeed({ org, repos: modalRepos });
    if (seq !== previewSeq) return;
    modalRepos = spec.repos;
    if (hint.dataset.error) {
      hint.textContent = "";
      delete hint.dataset.error;
    }
  } catch (error) {
    if (seq !== previewSeq) return;
    if (fallback) modalRepos = fallback;
    hint.textContent = error.message;
    hint.dataset.error = "1";
  }
  renderRepoTags();
}

function renderRepoTags() {
  const container = document.getElementById("gh-repo-tags");
  container.innerHTML = modalRepos
    .map(
      (repo, index) =>
        `<button type="button" class="field-chip" data-index="${index}" title="Remove">${escapeHtml(repo)} ✕</button>`
    )
    .join("");
  container.querySelectorAll("[data-index]").forEach((chip) => {
    chip.addEventListener("click", () => {
      modalRepos.splice(Number(chip.dataset.index), 1);
      renderRepoTags();
    });
  });
  renderRepoSuggestions();
}

async function renderRepoSuggestions() {
  const container = document.getElementById("gh-repo-suggest");
  const org = document.getElementById("gh-feed-form").org.value.trim();
  const cached = repoCache.get(org.toLowerCase());
  container.innerHTML = "";
  if (!cached) return;
  let repos;
  try {
    repos = await cached;
  } catch {
    return;
  }
  const chosen = new Set(modalRepos.map((repo) => repo.toLowerCase()));
  const suggestions = repos
    .filter((repo) => !repo.archived && !chosen.has(repo.name.toLowerCase()))
    .slice(0, SUGGESTED_REPOS);
  if (suggestions.length === 0) return;
  container.innerHTML =
    '<span class="format-fields-label">recent pushes</span>' +
    suggestions
      .map(
        (repo) =>
          `<button type="button" class="gh-suggest" data-name="${escapeHtml(repo.name)}" title="${escapeHtml(
            repo.description || repo.full_name
          )}">+ ${escapeHtml(repo.name)}</button>`
      )
      .join("");
  container.querySelectorAll("[data-name]").forEach((button) => {
    button.addEventListener("click", () => addRepos(button.dataset.name));
  });
}

async function loadRepoOptions() {
  const org = document.getElementById("gh-feed-form").org.value.trim();
  const hint = document.getElementById("gh-repo-hint");
  const datalist = document.getElementById("gh-repo-options");
  datalist.innerHTML = "";
  if (!org || !available) {
    hint.textContent = "";
    renderRepoSuggestions();
    return;
  }
  const key = org.toLowerCase();
  if (!repoCache.has(key)) repoCache.set(key, api.githubOrgRepos(org));
  hint.textContent = "loading repositories…";
  delete hint.dataset.error;
  try {
    const repos = await repoCache.get(key);
    if (document.getElementById("gh-feed-form").org.value.trim().toLowerCase() !== key) return;
    datalist.innerHTML = repos
      .map(
        (repo) =>
          `<option value="${escapeHtml(repo.name)}">${escapeHtml(
            (repo.archived ? "[archived] " : "") + (repo.description || "")
          )}</option>`
      )
      .join("");
    hint.textContent = `${plural(repos.length, "repository", "repositories")} in ${org}, most recently pushed first.`;
  } catch (error) {
    repoCache.delete(key);
    hint.textContent = `Could not list the repositories: ${error.message}`;
  }
  renderRepoSuggestions();
}

function renderKindPicker() {
  const container = document.getElementById("gh-kind-picker");
  container.innerHTML = KIND_ORDER.map(
    (kind) =>
      `<button type="button" class="chip${modalKinds.has(kind) ? " is-active" : ""}" data-kind="${kind}">${escapeHtml(
        KIND_LABELS[kind]
      )}</button>`
  ).join("");
  container.querySelectorAll("[data-kind]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const kind = chip.dataset.kind;
      if (modalKinds.has(kind)) modalKinds.delete(kind);
      else modalKinds.add(kind);
      chip.classList.toggle("is-active", modalKinds.has(kind));
    });
  });
}

// `preset` pre-fills a new feed (e.g. from refinements); `feed` edits one.
function openFeedModal(feed = null, preset = null) {
  editingId = feed ? feed.id : null;
  const source = feed || preset || {};
  const form = document.getElementById("gh-feed-form");
  form.reset();
  document.getElementById("gh-feed-modal-title").textContent = feed ? "Edit feed" : "New feed";
  form.name.value = source.name || "";
  form.org.value = source.org || (feeds.length ? feeds[feeds.length - 1].org : "");
  form.hide_bots.checked = source.hide_bots ?? true;
  form.hide_own.checked = Boolean(source.hide_own);
  form.description.value = source.description || "";
  document.getElementById("gh-repo-entry").value = "";
  modalRepos = [...(source.repos || [])];
  modalKinds = new Set(source.event_kinds || []);
  renderRepoTags();
  renderKindPicker();
  loadRepoOptions();
  openModal("gh-feed-modal");
}

async function submitFeed(event) {
  event.preventDefault();
  const form = event.target;
  const entry = document.getElementById("gh-repo-entry");
  if (entry.value.trim()) {
    addRepos(entry.value);
    entry.value = "";
  }
  const payload = {
    name: form.name.value.trim(),
    org: form.org.value.trim(),
    repos: modalRepos,
    event_kinds: KIND_ORDER.filter((kind) => modalKinds.has(kind)),
    hide_own: form.hide_own.checked,
    hide_bots: form.hide_bots.checked,
    description: form.description.value.trim(),
  };
  const saveBtn = document.getElementById("gh-feed-save-btn");
  saveBtn.disabled = true;
  try {
    if (editingId) {
      const updated = await api.updateGithubFeed(editingId, payload);
      feeds = feeds.map((item) => (item.id === updated.id ? updated : item));
      closeModal("gh-feed-modal");
      renderFeedList();
      toast("Feed updated", "success");
      if (view.feed?.id === updated.id) openFeed(updated); // same feed: the mark is untouched
    } else {
      const created = await api.createGithubFeed(payload);
      feeds.push(created);
      closeModal("gh-feed-modal");
      renderFeedList();
      toast("Feed created", "success");
      openFeed(created);
    }
  } catch (error) {
    toast(error.message, "error");
  } finally {
    saveBtn.disabled = false;
  }
}

function saveRefinementsAsFeed() {
  const feed = view.feed;
  if (!feed) return;
  const pick = (field) => view.refinements.filter((r) => r.field === field).map((r) => r.value);
  const repos = pick("repo").map(shortRepo);
  const kinds = pick("kind");
  const label = view.refinements
    .filter((r) => r.field !== "actor")
    .map(refinementLabel)
    .join(", ");
  openFeedModal(null, {
    ...feed,
    name: `${feed.name} · ${label}`.slice(0, 80),
    repos: repos.length ? repos : feed.repos,
    event_kinds: kinds.length ? kinds : feed.event_kinds,
    description: "",
  });
  if (pick("actor").length) toast("The author refinement is not saved in the feed", "info");
}

// ----- lifecycle -------------------------------------------------------

function onViewChange(name) {
  const wasVisible = visible;
  visible = name === "github";
  if (wasVisible && !visible) markActiveSeen();
  if (!wasVisible && visible && view.feed) {
    // coming back: what was shown before counts as seen now (the mark moved
    // on leaving), so the server flags only what arrived since
    loadActivity();
  }
}

export function setGithubAvailability(isAvailable) {
  available = isAvailable;
}

// Server text with `code` spans and bare URLs, made safe and clickable.
function richText(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/https:\/\/[^\s<]+[^\s<.,;:)]/g, (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
}

// Ask the server what the token can do and say it where the user looks: the
// sidebar box and the "github" dot in the top bar.
async function checkToken() {
  const box = document.getElementById("gh-token-hint");
  let status;
  try {
    status = await api.githubToken();
  } catch {
    return; // backend down: main.js already shows it
  }
  box.hidden = status.problems.length === 0;
  box.innerHTML = status.problems.map((problem) => `<p>${richText(problem)}</p>`).join("");
  if (!status.usable) {
    const dot = document.getElementById("github-status");
    dot.classList.remove("is-ok");
    dot.classList.add("is-down");
    dot.title = status.problems[0] || "GitHub token unusable";
  }
}

export async function initGithub() {
  setupTooltips(document.getElementById("gh-timeline"));

  makeSortable(document.getElementById("gh-feed-list"), {
    itemSelector: ".filter-item",
    axis: "list",
    onReorder: persistOrder,
  });

  document.getElementById("gh-feed-new-btn").addEventListener("click", () => openFeedModal());
  document.getElementById("gh-feed-empty-btn").addEventListener("click", () => openFeedModal());
  document.getElementById("gh-feed-form").addEventListener("submit", submitFeed);
  document.getElementById("gh-feed-form").org.addEventListener("change", () => {
    loadRepoOptions();
    normalizeRepos(); // the org decides which "owner/name" entries are valid
  });

  const entry = document.getElementById("gh-repo-entry");
  entry.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addRepos(entry.value);
      entry.value = "";
    } else if (event.key === "Backspace" && !entry.value && modalRepos.length) {
      modalRepos.pop();
      renderRepoTags();
    }
  });
  // picking from the datalist fires input with the full name
  entry.addEventListener("input", (event) => {
    if (event.inputType === "insertReplacementText") {
      addRepos(entry.value);
      entry.value = "";
    }
  });
  entry.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text") || "";
    if (/[\s,]/.test(text.trim())) {
      event.preventDefault();
      addRepos(text);
    }
  });

  document.getElementById("gh-refresh-btn").addEventListener("click", () => loadActivity());
  document.getElementById("gh-seen-btn").addEventListener("click", () => {
    markActiveSeen();
    if (view.activity) {
      view.activity.events.forEach((event) => (event.is_new = false));
      view.activity.unseen = 0;
    }
    renderActivity();
  });
  document.getElementById("gh-auto-btn").addEventListener("click", () => {
    storageSet(AUTO_KEY, autoOn() ? "0" : "1");
    scheduleAuto();
    toast(autoOn() ? "Auto refresh every 60 s" : "Auto refresh off");
  });
  document.getElementById("gh-refine-clear-btn").addEventListener("click", () => {
    view.refinements = [];
    renderActivity();
  });
  document.getElementById("gh-refine-save-btn").addEventListener("click", saveRefinementsAsFeed);

  document.addEventListener("view:change", (event) => onViewChange(event.detail));
  window.addEventListener("pagehide", () => {
    if (visible) markActiveSeen({ keepalive: true });
  });

  scheduleAuto();
  setInterval(pollTick, POLL_TICK_MS);

  checkToken();
  feeds = await api.listGithubFeeds();
  renderFeedList();
  refreshUnseen();
}
