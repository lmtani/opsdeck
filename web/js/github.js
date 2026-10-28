// GitHub view: saved feeds sidebar, org/repo activity timeline grouped by
// day, client-side refinements (repo / kind / author / day), "new since the
// last visit" marks and the optional 60 s auto-refresh. The "contributors"
// mode (github-contributors.js) shows the same feed as daily volume per person.
//
// Seen marks: the server flags each event of a saved feed as `is_new` when
// it is newer than the feed's last_seen_at, and counts them in `unseen`.
// Leaving the feed (another feed, another tab, closing the page) or
// "✓ visto" moves the mark to the newest event shown.

import { api } from "./api.js";
import { makeSortable } from "./dnd.js";
import { createContextDrawer, hasContext } from "./github-context.js";
import { createContributorsView, dayKey } from "./github-contributors.js";
import {
  announce,
  anyNotifying,
  confirmEnabled,
  forgetFeed,
  isNotifying,
  notificationsBlocked,
  toggleNotifications,
} from "./notifications.js";
import { closeModal, confirmDanger, escapeHtml, LOCALE, openModal, toast } from "./ui.js";

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
const FIELD_LABELS = { repo: "repo", kind: "kind", actor: "author", day: "day" };
const AUTO_REFRESH_MS = 60_000;
const UNSEEN_REFRESH_MS = 5 * 60_000; // badges only, while the page is visible
const NOTIFY_REFRESH_MS = 2 * 60_000; // when some feed notifies, also in the background
const POLL_TICK_MS = 60_000;
const AUTO_KEY = "opsdeck.github.auto";
const MODE_KEY = "opsdeck.github.mode";
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
let polling = true; // the "live" switch, saved in the backend settings
let drawer = null; // side panel with an event's full context (github-context.js)
let contributors = null; // the contributors mode (github-contributors.js)
let mode = "timeline"; // or "contributors"

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
  if (field === "day") return dayKey(new Date(event.created_at));
  return field === "repo" ? event.repo : field === "kind" ? event.kind : event.actor;
}

function refinementLabel({ field, value }) {
  if (field === "repo") return shortRepo(value);
  if (field === "kind") return KIND_LABELS[value] || value;
  if (field === "day") {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(LOCALE, {
      weekday: "short",
      day: "2-digit",
      month: "short",
    });
  }
  return value;
}

// the refinements that map onto feed fields (and can be saved as a feed)
const isFeedField = (field) => field === "repo" || field === "kind";

function matches(event, ignoreField = null) {
  const groups = {};
  for (const { field, value } of view.refinements) {
    if (field !== ignoreField) {
      groups[field] ??= [];
      groups[field].push(value);
    }
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
  // the installed app also shows the count on its icon in the dock/taskbar
  if ("setAppBadge" in navigator) {
    const update = total ? navigator.setAppBadge(total) : navigator.clearAppBadge();
    update.catch(() => {
      /* not installed or not supported */
    });
  }
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
        notifying
          ? polling
            ? ' <span class="gh-bell-on" title="Desktop notifications on">🔔</span>'
            : ' <span class="gh-bell-on is-paused" title="Desktop notifications on, waiting for live updates to resume">🔔</span>'
          : ""
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
  if (!polling) return;
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
  if (outcome === "on") confirmEnabled(feed, { paused: !polling }, openFromNotification);
  const testHint =
    "No test notification on screen? Check the system's notification settings for this browser.";
  const messages = {
    on: polling
      ? [`Notifications for “${feed.name}” are on. ${testHint}`, "success"]
      : [
          `Notifications for “${feed.name}” are on; they start when live updates resume. ${testHint}`,
          "info",
        ],
    off: [`Notifications for “${feed.name}” are off.`, "info"],
    unsupported: [
      "This browser cannot show notifications here: they need HTTPS or localhost.",
      "error",
    ],
    blocked: [
      notificationsBlocked()
        ? "Notifications are blocked for this site. Allow them in the browser's site settings, then try again."
        : "Notifications were not allowed.",
      "error",
    ],
  };
  const [message, kind] = messages[outcome];
  toast(message, kind, outcome === "on" ? 8000 : undefined);
  renderFeedList();
}

// Badges refresh every 5 min while the page is visible; with any feed
// notifying, every 2 min even in a background tab. Nothing runs while the
// "live" switch is off.
let lastPoll = Date.now();

function pollTick() {
  if (!polling) return;
  const notifying = anyNotifying(feeds.map((feed) => feed.id));
  if (document.hidden && !notifying) return;
  const every = notifying ? NOTIFY_REFRESH_MS : UNSEEN_REFRESH_MS;
  if (Date.now() - lastPoll < every) return;
  lastPoll = Date.now();
  refreshUnseen();
}

// ----- timeline --------------------------------------------------------

// Placeholder shaped like the timeline: a day header, its histogram and
// event rows, swept by one shimmer.
const SKELETON_WIDTHS = [62, 48, 71, 55, 66, 40, 58, 69, 45];

function showSkeleton() {
  const bars = Array.from(
    { length: 48 },
    (_, index) =>
      `<i style="--i:${index};--h:${Math.round(20 + 70 * Math.abs(Math.sin(index * 0.7) * Math.cos(index * 0.23)))}%"></i>`
  ).join("");
  const rows = SKELETON_WIDTHS.map(
    (width, index) => `
      <div class="gh-skel-row" style="--i:${index}">
        <span class="gh-skel gh-skel-time"></span><span class="gh-skel gh-skel-avatar"></span>
        <span class="gh-skel gh-skel-chip"></span><span class="gh-skel gh-skel-line" style="--w:${width}%"></span>
      </div>`
  ).join("");
  document.getElementById("gh-timeline").innerHTML = `
    <div class="gh-skeleton" role="status" aria-label="Loading activity">
      <span class="gh-skel gh-skel-day"></span>
      <div class="gh-skel-hist">${bars}</div>
      ${rows}
    </div>`;
}

// ----- busy state ------------------------------------------------------

const busy = new Set(); // what is loading: "timeline", "contributors"

function setBusy(what, on) {
  if (on) busy.add(what);
  else busy.delete(what);
  document.getElementById("gh-progress").hidden = busy.size === 0;
  document.getElementById("gh-refresh-btn").classList.toggle("is-busy", busy.size > 0);
}

// What the timeline shows of an activity, to skip rebuilding it when a
// refresh brings nothing new.
function activitySignature(activity) {
  return JSON.stringify(
    activity.events.map((e) => [e.id, e.count, e.state, e.title, e.is_new, e.source])
  );
}

function openFeed(feed) {
  if (view.feed && view.feed.id !== feed.id) markActiveSeen();
  drawer?.close();
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
  contributors.setTimelineStart(null);
  if (mode === "contributors") contributors.open(feed);
}

async function loadActivity({ initial = false } = {}) {
  const feed = view.feed;
  if (!feed) return;
  const seq = ++requestSeq;
  setBusy("timeline", true);
  try {
    const activity = await api.runFeed(feed.id);
    if (seq !== requestSeq || view.feed?.id !== feed.id) return;
    const signature = activitySignature(activity);
    const changed = signature !== view.signature;
    // events this refresh brought, to set them apart as they appear
    const shown = view.activity ? new Set(view.activity.events.map((e) => e.id)) : null;
    const arrived = new Set(
      shown ? activity.events.filter((e) => !shown.has(e.id)).map((e) => e.id) : []
    );
    view.activity = activity;
    view.signature = signature;
    unseen[feed.id] = activity.unseen;
    contributors.setTimelineStart(timelineStart(activity));
    renderFeedList();
    if (initial || !shown) renderActivity({ animate: true });
    else if (changed) renderActivity({ arrived });
    else renderMeta();
  } catch (error) {
    if (seq !== requestSeq || view.feed?.id !== feed.id) return;
    if (initial || !view.activity) {
      view.activity = null;
      if (mode === "timeline")
        document.getElementById("gh-meta").textContent = `${feed.org} · failed`;
      document.getElementById("gh-timeline").innerHTML =
        `<div class="error-box"><strong>GitHub query failed</strong>${richText(error.message)}</div>`;
    } else {
      toast(`Could not refresh the feed: ${error.message}`, "error");
    }
  } finally {
    if (seq === requestSeq) setBusy("timeline", false);
  }
}

function renderActivity({ animate = false, arrived = null } = {}) {
  if (!view.activity) return;
  renderKinds();
  renderRefineBar();
  renderWarnings();
  renderTimeline(animate, arrived);
  renderMeta();
}

// The first day the timeline shows: where the events API's coverage starts,
// else the oldest event; null when nothing came back.
function timelineStart(activity) {
  if (activity.coverage_start) return new Date(activity.coverage_start);
  const times = activity.events.map((event) => time(event.created_at)).filter(Boolean);
  return times.length ? new Date(Math.min(...times)) : null;
}

function renderMeta() {
  if (mode === "contributors") return renderContributorsMeta();
  const { feed, activity } = view;
  if (!activity) return;
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
    parts.push(
      `updated ${new Date(activity.fetched_at).toLocaleTimeString(LOCALE, { hourCycle: "h23" })}`
    );
  }
  if (activity.rate_remaining != null) {
    parts.push(`quota ${activity.rate_remaining}/${activity.rate_limit}`);
  }
  document.getElementById("gh-meta").textContent = parts.join(" · ");
}

function renderContributorsMeta() {
  const feed = view.feed;
  if (!feed) return;
  const meta = contributors.meta();
  const parts = [feed.org, feed.repos.length ? plural(feed.repos.length, "repo") : "whole org"];
  if (!meta) {
    parts.push(contributors.busy ? "querying…" : "failed");
  } else {
    parts.push(`${plural(meta.total, "contribution")} in ${contributors.days} days`);
    parts.push(
      `updated ${new Date(meta.fetched_at).toLocaleTimeString(LOCALE, { hourCycle: "h23" })}`
    );
    if (meta.rate_remaining != null) parts.push(`quota ${meta.rate_remaining}/${meta.rate_limit}`);
  }
  document.getElementById("gh-meta").textContent = parts.join(" · ");
}

// ----- view mode -------------------------------------------------------

function setMode(next) {
  mode = next;
  storageSet(MODE_KEY, next);
  const contributorsMode = next === "contributors";
  document.querySelectorAll("#gh-mode [data-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === next));
  });
  document.getElementById("gh-timeline-view").hidden = contributorsMode;
  document.getElementById("gh-contributors").hidden = !contributorsMode;
  document.getElementById("gh-range").hidden = !contributorsMode;
  document.getElementById("gh-auto-btn").hidden = contributorsMode;
  document.getElementById("gh-seen-btn").hidden = contributorsMode;
  renderRange();
  hideTooltip();
  if (contributorsMode && view.feed) contributors.open(view.feed);
  if (view.feed) renderMeta();
}

function renderRange() {
  document.querySelectorAll("#gh-range [data-days]").forEach((button) => {
    button.setAttribute("aria-pressed", String(Number(button.dataset.days) === contributors.days));
  });
}

// A contributors cell opened in the timeline: that person on that day.
function openPersonDay({ login, day }) {
  view.refinements = [
    ...view.refinements.filter((r) => isFeedField(r.field)),
    { field: "actor", value: login },
    { field: "day", value: day },
  ];
  setMode("timeline");
  renderActivity();
  window.scrollTo({ top: 0, behavior: "smooth" });
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
  document.getElementById("gh-refine-save-btn").hidden = !view.refinements.some((r) =>
    isFeedField(r.field)
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
    (rest > 0
      ? `<div class="gh-tooltip-sub">+ ${plural(rest, "other contributor", "other contributors")}</div>`
      : "") +
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

// Highlight one person's share of each half hour (null clears it). Their
// bars are drawn over the dimmed day, on the same scale.
function focusHistogram(histogram, person, peak) {
  histogram.classList.toggle("is-focus", Boolean(person));
  histogram.querySelectorAll("[data-bin]").forEach((column) => {
    const weight = person?.bins.get(Number(column.dataset.bin)) || 0;
    const height =
      weight && peak ? Math.min(100, Math.max(6, Math.round((weight / peak) * 100))) : 0;
    column.style.setProperty("--mine", `${height}%`);
  });
}

// ----- per-day contributors ----------------------------------------------

const PEOPLE_SHOWN = 8;

// Who was active on each day, from the events that match every refinement
// but the author one: the strip works like a facet, so picking a person
// keeps the others visible (dimmed) and one click away.
function dayContributors(events) {
  const days = new Map(); // toDateString() -> Map(login -> person)
  for (const event of events) {
    const date = new Date(event.created_at);
    const key = date.toDateString();
    if (!days.has(key)) days.set(key, new Map());
    const people = days.get(key);
    const person = people.get(event.actor) || {
      login: event.actor,
      avatar: event.actor_avatar,
      count: 0,
      kinds: new Map(),
      repos: new Map(),
      bins: new Map(),
      first: date,
      last: date,
    };
    const weight = event.count || 1;
    const bin = Math.floor((date.getHours() * 60 + date.getMinutes()) / BIN_MINUTES);
    person.count += weight;
    person.avatar ||= event.actor_avatar;
    person.kinds.set(event.kind, (person.kinds.get(event.kind) || 0) + weight);
    person.repos.set(event.repo, (person.repos.get(event.repo) || 0) + weight);
    person.bins.set(bin, (person.bins.get(bin) || 0) + weight);
    if (date < person.first) person.first = date;
    if (date > person.last) person.last = date;
    people.set(event.actor, person);
  }
  return days;
}

const hhmm = (date) =>
  date.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

function personTooltip(person, { isYou, active, filtering }) {
  const span =
    person.first.getTime() === person.last.getTime()
      ? `at ${hhmm(person.first)}`
      : `${hhmm(person.first)}–${hhmm(person.last)}`;
  const kinds = KIND_ORDER.filter((kind) => person.kinds.has(kind))
    .map(
      (kind) =>
        `<span class="gh-tip-kind kind-${kind}">${escapeHtml(KIND_LABELS[kind])} ${person.kinds.get(kind)}</span>`
    )
    .join("");
  const repos = [...person.repos.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([repo]) => shortRepo(repo));
  const repoLine = repos.slice(0, 3).join(", ") + (repos.length > 3 ? ` +${repos.length - 3}` : "");
  const hint = active
    ? "click to show everyone again"
    : filtering
      ? "click to add them to the author filter"
      : "click to show only their events";
  return (
    `<div class="gh-tooltip-main">${escapeHtml(person.login)}${isYou ? " (you)" : ""}</div>` +
    `<div class="gh-tooltip-sub">${plural(person.count, "event")} · ${span}</div>` +
    (kinds ? `<div class="gh-tip-kinds">${kinds}</div>` : "") +
    `<div class="gh-tooltip-sub gh-tip-repos">${escapeHtml(repoLine)}</div>` +
    `<div class="gh-tooltip-sub gh-tip-hint">${hint}</div>`
  );
}

// The strip under a day's histogram: one avatar per contributor, busiest
// first (an order to read by, not a ranking), with how many events each.
function contributorsNode(day, people, { histogram, peak, partial }) {
  const viewer = (view.activity.viewer || "").toLowerCase();
  const chosen = new Set(view.refinements.filter((r) => r.field === "actor").map((r) => r.value));
  const sorted = [...people.values()].sort(
    (a, b) => b.count - a.count || a.login.localeCompare(b.login)
  );
  // the first few, plus anyone picked in the author filter
  const extra = sorted.filter(
    (person, index) => index >= PEOPLE_SHOWN && !chosen.has(person.login)
  );
  const repos = new Set(sorted.flatMap((person) => [...person.repos.keys()]));
  view.peopleOpen ||= new Set();

  const node = document.createElement("div");
  node.className = `gh-people${view.peopleOpen.has(day) ? " is-open" : ""}`;
  node.innerHTML = `
    <div class="gh-people-list">
      ${sorted
        .map((person) => {
          const isYou = viewer && person.login.toLowerCase() === viewer;
          const state = [
            isYou ? "is-you" : "",
            chosen.has(person.login) ? "is-active" : chosen.size ? "is-dimmed" : "",
            extra.includes(person) ? "is-extra" : "",
          ]
            .filter(Boolean)
            .join(" ");
          const avatar = person.avatar
            ? `<img class="gh-person-avatar" src="${escapeHtml(avatarUrl(person.avatar))}" alt="" loading="lazy" />`
            : `<span class="gh-person-avatar">${escapeHtml(person.login.slice(0, 1).toUpperCase())}</span>`;
          return `
            <button type="button" class="gh-person ${state}" data-person="${escapeHtml(person.login)}"
              aria-pressed="${chosen.has(person.login)}"
              aria-label="${escapeHtml(`${person.login}, ${plural(person.count, "event")}`)}">
              ${avatar}<span class="gh-person-count">${person.count}</span>
            </button>`;
        })
        .join("")}
      ${
        extra.length
          ? `<button type="button" class="gh-people-more" data-more aria-expanded="${view.peopleOpen.has(day)}"></button>`
          : ""
      }
    </div>
    <div class="gh-people-total">
      ${plural(sorted.length, "contributor")} · ${plural(repos.size, "repo")}${
        partial ? ' <span class="gh-people-partial" data-tip="partial day">· partial</span>' : ""
      }
    </div>`;

  const partialNote = node.querySelector(".gh-people-partial");
  if (partialNote) {
    tooltipContent.set(
      partialNote,
      () =>
        '<div class="gh-tooltip-main">Partial day</div>' +
        '<div class="gh-tooltip-sub">It starts before the oldest event GitHub returned, so some activity, and some contributors, may be missing.</div>'
    );
  }

  const more = node.querySelector("[data-more]");
  const labelMore = () => {
    const open = node.classList.contains("is-open");
    more.textContent = open ? "less" : `+${extra.length}`;
    more.title = open
      ? "Show fewer contributors"
      : `Show ${plural(extra.length, "more contributor", "more contributors")}`;
    more.setAttribute("aria-expanded", String(open));
  };
  if (more) {
    labelMore();
    more.addEventListener("click", () => {
      node.classList.toggle("is-open");
      if (node.classList.contains("is-open")) view.peopleOpen.add(day);
      else view.peopleOpen.delete(day);
      labelMore();
    });
  }

  node.querySelectorAll("[data-person]").forEach((button) => {
    const person = people.get(button.dataset.person);
    const isYou = button.classList.contains("is-you");
    tooltipContent.set(button, () =>
      personTooltip(person, { isYou, active: chosen.has(person.login), filtering: chosen.size > 0 })
    );
    const focus = () => focusHistogram(histogram, person, peak);
    const blur = () => focusHistogram(histogram, null, peak);
    button.addEventListener("mouseenter", focus);
    button.addEventListener("mouseleave", blur);
    button.addEventListener("focus", focus);
    button.addEventListener("blur", blur);
    button.addEventListener("click", () => toggleRefinement("actor", person.login));
  });
  return node;
}

function renderTimeline(animate, arrived = null) {
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
  const contributors = dayContributors(
    view.activity.events.filter((event) => matches(event, "actor"))
  );

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
      const histogram = histogramNode(date, volume.get(day), peak, coverageStart);
      container.appendChild(histogram);
      const dayStart = new Date(date).setHours(0, 0, 0, 0);
      container.appendChild(
        contributorsNode(day, contributors.get(day), {
          histogram,
          peak,
          partial: coverageStart !== null && dayStart < coverageStart,
        })
      );
      lastDay = day;
    }
    container.appendChild(eventNode(event, index, animate, arrived?.has(event.id)));
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

function eventNode(event, index, animate, isArrived = false) {
  const date = new Date(event.created_at);
  const viewer = (view.activity.viewer || "").toLowerCase();
  const isYou = viewer && event.actor.toLowerCase() === viewer;

  const node = document.createElement("div");
  node.className = `gh-event${event.is_new ? " is-new" : ""}${animate ? "" : " no-anim"}${
    isArrived ? " is-arrived" : ""
  }${drawer?.currentId() === event.id ? " is-current" : ""}`;
  node.dataset.id = event.id;
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
  node
    .querySelector(".gh-link")
    .addEventListener("click", (clickEvent) => clickEvent.stopPropagation());
  const live = node.querySelector("[data-live]");
  if (live) tooltipContent.set(live, liveTooltip);
  node.querySelector(".gh-row").addEventListener("click", () => {
    // with the panel open, a click shows that event there instead
    if (drawer?.isOpen() && hasContext(event)) {
      openContext(event);
      return;
    }
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
      row(
        "commits",
        heads.map((sha) => link(`${repoUrl}/commit/${sha}`, sha.slice(0, 7))).join(" ")
      );
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
    (comments || (event.body ? `<div class="gh-body">${escapeHtml(event.body)}</div>` : "")) +
    (hasContext(event)
      ? '<button type="button" class="btn btn-ghost btn-sm gh-context-btn">Full context →</button>'
      : "");
  detail.querySelector(".gh-context-btn")?.addEventListener("click", (click) => {
    click.stopPropagation();
    openContext(event);
  });
  return detail;
}

// ----- full context panel ------------------------------------------------

function markCurrent(id) {
  for (const node of document.querySelectorAll("#gh-timeline .gh-event")) {
    node.classList.toggle("is-current", node.dataset.id === id);
  }
}

function openContext(event) {
  drawer.open(event);
  markCurrent(event.id);
}

// ↑/↓ in the panel: the neighbouring event, among those on screen that
// have something to open
function stepContext(delta) {
  const candidates =
    view.activity?.events.filter((event) => matches(event) && hasContext(event)) || [];
  const index = candidates.findIndex((event) => event.id === drawer.currentId());
  const next = candidates[index + delta];
  if (!next) return;
  openContext(next);
  document
    .querySelector(`#gh-timeline .gh-event[data-id="${CSS.escape(next.id)}"]`)
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ----- tooltips --------------------------------------------------------

// One floating tooltip for the timeline, shown over [data-tip] elements whose
// text is cut off by the ellipsis, and always over histogram bins, whose
// content comes from the tooltipContent builders.
const TOOLTIP_DELAY_MS = 220;
let tooltip = null;
let tooltipTarget = null;
let tooltipTimer = null;
// elements whose tooltip is HTML built on demand (histogram bins, people); the
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

const TOOLTIP_TARGETS =
  "[data-tip], [data-bin], [data-live], [data-person], [data-cell], [data-mix], [data-team], [data-tooltip]";

function setupTooltips(...containers) {
  tooltip = document.createElement("div");
  tooltip.className = "gh-tooltip";
  tooltip.setAttribute("role", "tooltip");
  document.body.appendChild(tooltip);

  const arm = (target) => {
    if (target === tooltipTarget) return;
    hideTooltip();
    if (!target) return;
    if (!tooltipContent.has(target) && !isTruncated(target)) return;
    tooltipTarget = target;
    tooltipTimer = setTimeout(() => {
      if (tooltipTarget === target && target.isConnected) showTooltip(target);
    }, TOOLTIP_DELAY_MS);
  };
  for (const container of containers) {
    container.addEventListener("mouseover", (event) => arm(event.target.closest(TOOLTIP_TARGETS)));
    container.addEventListener("mouseleave", hideTooltip);
    // keyboard users get the same tooltips (the mix bar of each person is focusable)
    container.addEventListener("focusin", (event) => arm(event.target.closest(TOOLTIP_TARGETS)));
    container.addEventListener("focusout", hideTooltip);
  }
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
  const button = document.getElementById("gh-auto-btn");
  button.classList.toggle("is-on", autoOn() && polling);
  button.disabled = !polling;
  button.title = polling ? "Refresh every 60 s" : "Live updates are paused";
  if (!autoOn() || !polling) return;
  autoTimer = setInterval(() => {
    if (visible && !document.hidden && view.feed) loadActivity();
  }, AUTO_REFRESH_MS);
}

// ----- live switch -----------------------------------------------------

function renderPolling() {
  const button = document.getElementById("gh-poll-btn");
  button.classList.toggle("is-on", polling);
  button.setAttribute("aria-pressed", String(polling));
  button.textContent = polling ? "● live" : "⏸ paused";
  button.title = polling
    ? "Badges, notifications and auto refresh update in the background. Click to pause."
    : "Paused: feeds only update when you open or refresh them. Click to resume.";
}

function applyPolling(on) {
  polling = on;
  renderPolling();
  scheduleAuto();
  renderFeedList();
}

async function togglePolling() {
  applyPolling(!polling);
  if (polling) {
    lastPoll = Date.now();
    refreshUnseen();
  }
  toast(
    polling
      ? "Live updates on: badges, notifications and auto refresh resume."
      : "Live updates paused. Feeds update only when you open or refresh them.",
    polling ? "success" : "info"
  );
  try {
    await api.saveSettings({ github_polling: polling });
  } catch {
    toast("Could not save the live updates setting; it applies to this page only.", "error");
  }
}

// ----- feed modal ------------------------------------------------------

// Repo names are normalized by the server (org prefix and URLs stripped,
// duplicates folded, invalid names rejected): the chips show what it
// answers, and an invalid entry is dropped with its message in the hint.
let previewSeq = 0;

async function addRepos(text) {
  const pieces = text
    .split(/[\s,]+/)
    .map((piece) => piece.trim())
    .filter(Boolean);
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
    .filter((r) => isFeedField(r.field))
    .map(refinementLabel)
    .join(", ");
  openFeedModal(null, {
    ...feed,
    name: `${feed.name} · ${label}`.slice(0, 80),
    repos: repos.length ? repos : feed.repos,
    event_kinds: kinds.length ? kinds : feed.event_kinds,
    description: "",
  });
  if (view.refinements.some((r) => !isFeedField(r.field))) {
    toast("The author and day refinements are not saved in the feed", "info");
  }
}

// ----- lifecycle -------------------------------------------------------

function onViewChange(name) {
  const wasVisible = visible;
  visible = name === "github";
  if (wasVisible && !visible) {
    markActiveSeen();
    drawer?.close();
  }
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
    .replace(
      /https:\/\/[^\s<]+[^\s<.,;:)]/g,
      (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`
    );
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
  const contributorsBox = document.getElementById("gh-contributors");
  setupTooltips(document.getElementById("gh-timeline"), contributorsBox);
  contributors = createContributorsView({
    container: contributorsBox,
    tooltips: tooltipContent,
    avatarUrl,
    onTimeline: openPersonDay,
    onBusy: (on) => setBusy("contributors", on),
    onLoaded: () => {
      hideTooltip();
      if (mode === "contributors") renderMeta();
    },
  });
  document.querySelectorAll("#gh-mode [data-mode]").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });
  document.querySelectorAll("#gh-range [data-days]").forEach((button) => {
    button.addEventListener("click", () => {
      contributors.setDays(Number(button.dataset.days));
      renderRange();
    });
  });
  setMode(storageGet(MODE_KEY) === "contributors" ? "contributors" : "timeline");
  drawer = createContextDrawer({
    labels: { kind: KIND_LABELS, state: STATE_LABELS },
    avatarUrl,
    onStep: stepContext,
    onClose: () => markCurrent(null),
  });

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

  document.getElementById("gh-refresh-btn").addEventListener("click", () => {
    if (mode === "contributors") contributors.refresh();
    loadActivity();
  });
  document.getElementById("gh-seen-btn").addEventListener("click", () => {
    markActiveSeen();
    if (view.activity) {
      for (const event of view.activity.events) event.is_new = false;
      view.activity.unseen = 0;
    }
    renderActivity();
  });
  document.getElementById("gh-poll-btn").addEventListener("click", togglePolling);
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

  try {
    polling = (await api.getSettings()).github_polling !== false;
  } catch {
    /* settings unavailable: stay live */
  }
  renderPolling();
  scheduleAuto();
  setInterval(pollTick, POLL_TICK_MS);

  checkToken();
  feeds = await api.listGithubFeeds();
  renderFeedList();
  refreshUnseen();
}
