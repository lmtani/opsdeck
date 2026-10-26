// Desktop notifications for GitHub feeds (Web Notifications API).
//
// Per browser, like the permission itself: which feeds notify, and for each
// one the newest event already announced, so nothing is announced twice and
// turning a feed on does not replay its backlog. The GitHub view decides when
// to check; this module asks permission, keeps that state and builds the
// message.

const ENABLED_KEY = "opsdeck.github.notify";
const MARK_PREFIX = "opsdeck.github.notified.";
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

export function isNotifying(feedId) {
  return read(ENABLED_KEY, []).includes(feedId) && notificationsSupported()
    && Notification.permission === "granted";
}

export function anyNotifying(feedIds) {
  return feedIds.some(isNotifying);
}

// Turn a feed's notifications on or off. Turning on asks for permission (it
// must run from a click) and starts counting from now. Returns the outcome so
// the caller can explain it: "on", "off", "unsupported", "blocked".
export async function toggleNotifications(feedId) {
  const enabled = read(ENABLED_KEY, []);
  if (enabled.includes(feedId) && Notification.permission === "granted") {
    write(ENABLED_KEY, enabled.filter((id) => id !== feedId));
    return "off";
  }
  if (!notificationsSupported()) return "unsupported";
  const permission = Notification.permission === "default"
    ? await Notification.requestPermission()
    : Notification.permission;
  if (permission !== "granted") return "blocked";
  write(ENABLED_KEY, [...new Set([...enabled, feedId])]);
  write(MARK_PREFIX + feedId, new Date().toISOString());
  return "on";
}

export function forgetFeed(feedId) {
  write(ENABLED_KEY, read(ENABLED_KEY, []).filter((id) => id !== feedId));
  try {
    localStorage.removeItem(MARK_PREFIX + feedId);
  } catch {
    /* nothing to forget */
  }
}

const time = (value) => Date.parse(value || "") || 0;

function shorten(text, max = MAX_LINE) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
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

// Announce the events of `activity` that are new and newer than the last
// announcement for this feed. Returns how many were announced.
export function announce(feed, activity, onOpen) {
  if (!isNotifying(feed.id)) return 0;
  const mark = time(read(MARK_PREFIX + feed.id, null));
  const fresh = activity.events.filter((event) => event.is_new && time(event.created_at) > mark);
  if (!fresh.length) return 0;
  const { title, body, icon } = composeNotification(feed.name, fresh);
  const notification = new Notification(title, {
    body,
    icon,
    tag: `opsdeck-feed-${feed.id}`, // a newer alert for the same feed replaces the old one
    renotify: true,
  });
  notification.onclick = () => {
    window.focus();
    onOpen(feed);
    notification.close();
  };
  const newest = fresh.reduce((max, event) => (time(event.created_at) > time(max) ? event.created_at : max), "");
  write(MARK_PREFIX + feed.id, newest);
  return fresh.length;
}
