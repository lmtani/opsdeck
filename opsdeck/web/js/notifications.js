// Desktop notifications (Web Notifications API) for GitHub feeds and watched
// log filters.
//
// Per browser, like the permission itself: which items notify (and, for a log
// filter, from which severity), and for each one a mark of what was already
// announced, so nothing is announced twice and turning an item on does not
// replay its backlog. The views decide when to check; this module asks
// permission, keeps that state and shows the message. createNotifier holds the
// state of one kind of item; the feed functions below are built on one.

const MAX_LINES = 3;
const MAX_LINE = 90;

function read(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: the preference just isn't remembered */
  }
}

export function notificationsSupported() {
  return "Notification" in window && window.isSecureContext;
}

export function notificationsBlocked() {
  return notificationsSupported() && Notification.permission === "denied";
}

const granted = () => notificationsSupported() && Notification.permission === "granted";

// `enabledKey` holds {id: setting} (a list of ids in older versions: each one
// reads as `true`), `markPrefix + id` the mark of what was announced, `tag`
// names an item's notification, so a newer one replaces it.
export function createNotifier({ enabledKey, markPrefix, tag }) {
  const settings = () => {
    const stored = read(enabledKey, {});
    return Array.isArray(stored) ? Object.fromEntries(stored.map((id) => [id, true])) : stored;
  };
  const forgetMark = (id) => {
    try {
      localStorage.removeItem(markPrefix + id);
    } catch {
      /* nothing to forget */
    }
  };
  const notifier = {
    // the item's setting while notifications can be shown, else null
    setting: (id) => (granted() ? (settings()[id] ?? null) : null),
    stored: (id) => settings()[id] ?? null,
    any: (ids) => ids.some((id) => notifier.setting(id) !== null),
    // Turn an item on (or change its setting). Asks for permission, so it must
    // run from a click. Returns "on", "unsupported" or "blocked".
    async enable(id, value, mark) {
      if (!notificationsSupported()) return "unsupported";
      const permission =
        Notification.permission === "default"
          ? await Notification.requestPermission()
          : Notification.permission;
      if (permission !== "granted") return "blocked";
      write(enabledKey, { ...settings(), [id]: value });
      write(markPrefix + id, mark);
      return "on";
    },
    disable(id) {
      const { [id]: _, ...rest } = settings();
      write(enabledKey, rest);
      forgetMark(id);
    },
    mark: (id) => read(markPrefix + id, null),
    setMark: (id, value) => write(markPrefix + id, value),
    show(id, { title, body, icon }, onClick) {
      const notification = new Notification(title, {
        body,
        icon,
        tag: `${tag}${id}`,
        renotify: true,
      });
      notification.onclick = () => {
        window.focus();
        onClick();
        notification.close();
      };
    },
  };
  return notifier;
}

// ----- GitHub feeds ----------------------------------------------------

const feeds = createNotifier({
  enabledKey: "opsdeck.github.notify",
  markPrefix: "opsdeck.github.notified.",
  tag: "opsdeck-feed-",
});

export const isNotifying = (feedId) => feeds.setting(feedId) !== null;
export const anyNotifying = (feedIds) => feeds.any(feedIds);

// Turn a feed's notifications on or off; on starts counting from now. Returns
// the outcome so the caller can explain it: "on", "off", "unsupported", "blocked".
export async function toggleNotifications(feedId) {
  if (isNotifying(feedId)) {
    feeds.disable(feedId);
    return "off";
  }
  return feeds.enable(feedId, true, new Date().toISOString());
}

export const forgetFeed = (feedId) => feeds.disable(feedId);

const time = (value) => Date.parse(value || "") || 0;

function shorten(text, max = MAX_LINE) {
  const clean = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

function avatar(url) {
  if (!url) return undefined;
  return url.includes("?") ? `${url}${url.endsWith("?") ? "" : "&"}s=96` : `${url}?s=96`;
}

function sentence(event) {
  return `${event.actor} ${event.summary}`;
}

// Notification text for a feed's fresh events (newest first).
//   one event:  "Core · alice merged PR #12"   body: PR title, then repo
//   several:    "Core · 3 new events"          body: up to 3 "actor summary" lines, "+N more"
export function composeNotification(feedName, events) {
  const [first] = events;
  if (events.length === 1) {
    const details = [first.title, first.repo].filter(Boolean).map((line) => shorten(line));
    return {
      title: shorten(`${feedName} · ${sentence(first)}`, 120),
      body: details.join("\n"),
      icon: avatar(first.actor_avatar),
    };
  }
  const total = events.reduce((sum, event) => sum + (event.count || 1), 0);
  const lines = events.slice(0, MAX_LINES).map((event) => `• ${shorten(sentence(event))}`);
  const rest = events.length - MAX_LINES;
  if (rest > 0) lines.push(`+${rest} more`);
  return {
    title: `${feedName} · ${total} new events`,
    body: lines.join("\n"),
    icon: avatar(first.actor_avatar), // the newest event's author
  };
}

// Sent right after a feed's notifications are turned on, so the user sees
// that they reach the desktop (or learns they don't, e.g. do-not-disturb).
export function confirmEnabled(feed, { paused }, onOpen) {
  feeds.show(
    feed.id,
    {
      title: `${feed.name} · notifications on`,
      body: paused
        ? "Live updates are paused in OpsDeck. New events will show up here once you resume them."
        : "New events in this feed will show up here while OpsDeck is open in a tab.",
    },
    () => onOpen(feed)
  );
}

// Announce the events of `activity` that are new and newer than the last
// announcement for this feed. Returns how many were announced.
export function announce(feed, activity, onOpen) {
  if (!isNotifying(feed.id)) return 0;
  const mark = time(feeds.mark(feed.id));
  const fresh = activity.events.filter((event) => event.is_new && time(event.created_at) > mark);
  if (!fresh.length) return 0;
  feeds.show(feed.id, composeNotification(feed.name, fresh), () => onOpen(feed));
  const newest = fresh.reduce(
    (max, event) => (time(event.created_at) > time(max) ? event.created_at : max),
    ""
  );
  feeds.setMark(feed.id, newest);
  return fresh.length;
}

// ----- watched log filters ---------------------------------------------
//
// A filter notifies from a severity: its setting is the level, its mark
// {since, count} what was announced of the count that started at `since` (the
// filter's last_seen_at, so "mark seen" starts a new count), or {pending:
// true} until the first count after turning it on, which is only noted.

const filters = createNotifier({
  enabledKey: "opsdeck.logs.notify",
  markPrefix: "opsdeck.logs.notified.",
  tag: "opsdeck-filter-",
});

export const LOG_LEVELS = [
  { value: "error", label: "errors", bands: ["error"] },
  { value: "warning", label: "warnings and errors", bands: ["error", "warning"] },
  { value: "any", label: "any new entry", bands: ["error", "warning", "info", "other"] },
];
export const DEFAULT_LOG_LEVEL = "error";
const BAND_NOUNS = {
  error: ["error", "errors"],
  warning: ["warning", "warnings"],
  info: ["info entry", "info entries"],
  other: ["debug entry", "debug entries"],
};

const levelOf = (value) => LOG_LEVELS.find((level) => level.value === value) || LOG_LEVELS[0];
const caught = (status, level) =>
  levelOf(level).bands.reduce((sum, band) => sum + (status?.[band] || 0), 0);
const noun = (band, count, extra = "") =>
  `${count}${extra} ${BAND_NOUNS[band][count === 1 ? 0 : 1]}`;

// The level a filter notifies from, or null.
export function filterNotifyLevel(filterId) {
  const value = filters.setting(filterId);
  return value === null ? null : levelOf(value).value;
}

export const levelLabel = (value) => levelOf(value).label;
export const anyFilterNotifying = (filterIds) => filters.any(filterIds);
export const forgetFilter = (filterId) => filters.disable(filterId);

// Turn a filter's notifications on from `level` ("off" turns them off), noting
// what its current count already holds. Returns "on", "off", "unsupported" or "blocked".
export async function setFilterNotifications(filterId, level, status) {
  if (level === "off") {
    filters.disable(filterId);
    return "off";
  }
  const mark = status ? { since: status.since, count: caught(status, level) } : { pending: true };
  return filters.enable(filterId, level, mark);
}

// Notification text for what a filter caught in the bands of its level:
//   "API errors · 3 new errors"            body: "since 11/03 14:02 · prod-logs"
//   "API errors · 5 new entries"           body: "2 errors · 3 warnings", then the line above
export function composeLogNotification(filter, status, level, { source, since }) {
  const plus = status.capped ? "+" : "";
  const bands = levelOf(level).bands.filter((band) => status[band] > 0);
  const where = [`since ${since}`, source].filter(Boolean).join(" · ");
  if (bands.length === 1) {
    return {
      title: shorten(`${filter.name} · ${noun(bands[0], status[bands[0]], `${plus} new`)}`, 120),
      body: where,
    };
  }
  const total = caught(status, level);
  return {
    title: shorten(`${filter.name} · ${total}${plus} new entries`, 120),
    body: `${bands.map((band) => noun(band, status[band])).join(" · ")}\n${where}`,
  };
}

// Announce a filter's count when the bands of its level grew since the last
// announcement. `looking` (the user has these entries on screen) only moves
// the mark. onOpen gets the most severe band with news. Returns how many new
// entries there were.
export function announceFilter(filter, status, details, onOpen) {
  const level = filterNotifyLevel(filter.id);
  if (!level || !status || status.problem) return 0;
  const count = caught(status, level);
  const mark = filters.mark(filter.id) || { pending: true };
  const fresh = mark.pending ? 0 : mark.since === status.since ? count - mark.count : count;
  filters.setMark(filter.id, { since: status.since, count });
  if (fresh <= 0 || details.looking) return 0;
  const band = levelOf(level).bands.find((name) => status[name] > 0);
  filters.show(filter.id, composeLogNotification(filter, status, level, details), () =>
    onOpen(filter, band)
  );
  return fresh;
}

// Sent right after a filter's notifications are turned on, like a feed's.
export function confirmFilterEnabled(filter, level, { paused }, onOpen) {
  const what = level === "any" ? "New entries" : `New ${levelLabel(level)}`;
  filters.show(
    filter.id,
    {
      title: `${filter.name} · notifications on`,
      body: paused
        ? `Live updates are paused in OpsDeck. ${what} will show up here once you resume them.`
        : `${what} in this filter will show up here while OpsDeck is open in a tab.`,
    },
    () => onOpen(filter, null)
  );
}
