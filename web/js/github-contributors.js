// The "contributors" mode of the GitHub tab: daily contributions per person
// (commits on default branches, PRs and issues opened, reviews) as a grid of
// one row per person and one column per day, under the team's daily volume.
// Unlike the timeline it is not capped by the events API, so it reaches weeks
// back. Kind and repo chips are client-side facets; clicking a cell lists
// what that person did that day and links it to the timeline.

import { api } from "./api.js";
import { escapeHtml, LOCALE } from "./ui.js";

export const CONTRIBUTION_LABELS = { commit: "commit", pr: "PR", review: "review", issue: "issue" };
const PLURAL_LABELS = { commit: "commits", pr: "PRs", review: "reviews", issue: "issues" };
const KINDS = Object.keys(CONTRIBUTION_LABELS);
const RANGES = [14, 28, 56];
const RANGE_KEY = "opsdeck.github.range";
const REPOS_SHOWN = 8;
const TIP_MAX_ROWS = 6;
const REVIEW_STATE_LABELS = {
  approved: "approved",
  changes_requested: "changes requested",
  commented: "commented",
  dismissed: "dismissed",
};

const DAY_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  weekday: "short",
  day: "2-digit",
  month: "short",
});
const WEEKDAY_FORMAT = new Intl.DateTimeFormat(LOCALE, { weekday: "narrow" });
const MONTH_FORMAT = new Intl.DateTimeFormat(LOCALE, { month: "short" });

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

const plural = (count, singular, pluralForm = `${singular}s`) =>
  `${count} ${count === 1 ? singular : pluralForm}`;
const shortRepo = (full) => full.split("/").slice(1).join("/") || full;
const sum = (entries) => entries.reduce((total, entry) => total + entry.count, 0);

// "YYYY-MM-DD" of a local date, the server's day keys
export function dayKey(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function parseDay(key) {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function daysBetween(since, until) {
  const days = [];
  for (let date = parseDay(since); dayKey(date) <= until; date.setDate(date.getDate() + 1)) {
    days.push({ key: dayKey(date), date: new Date(date) });
  }
  return days;
}

const hhmm = (value) =>
  new Date(value).toLocaleTimeString(LOCALE, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
// only github.com links are rendered as hrefs
const safeUrl = (url) => (String(url || "").startsWith("https://github.com/") ? url : "");

const isWeekend = (date) => date.getDay() === 0 || date.getDay() === 6;

function byKind(entries) {
  const counts = new Map(KINDS.map((kind) => [kind, 0]));
  for (const entry of entries) counts.set(entry.kind, (counts.get(entry.kind) || 0) + entry.count);
  return counts;
}

function grouped(entries, key) {
  const counts = new Map();
  for (const entry of entries) counts.set(key(entry), (counts.get(key(entry)) || 0) + entry.count);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function kindChips(counts) {
  return KINDS.filter((kind) => counts.get(kind))
    .map(
      (kind) =>
        `<span class="gh-tip-kind kind-${kind}">${escapeHtml(CONTRIBUTION_LABELS[kind])} ${counts.get(kind)}</span>`
    )
    .join("");
}

function rowsTable(rows, total) {
  const shown = rows.slice(0, TIP_MAX_ROWS);
  const rest = rows.length - shown.length;
  const restCount = total - shown.reduce((acc, [, count]) => acc + count, 0);
  return `<div class="gh-cg-tip-rows">${shown
    .map(([name, count]) => `<span>${escapeHtml(name)}</span><b>${count}</b>`)
    .join("")}${rest ? `<span>+${rest} more</span><b>${restCount}</b>` : ""}</div>`;
}

/**
 * @param {object} deps
 * @param {HTMLElement} deps.container where the view renders
 * @param {WeakMap<Element, () => string>} deps.tooltips rich tooltip builders (github.js)
 * @param {(url: string) => string} deps.avatarUrl sized avatar URL
 * @param {(drill: {login: string, day: string}) => void} deps.onTimeline open the timeline on a person's day
 * @param {() => void} deps.onLoaded called after each render (meta line refresh)
 */
export function createContributorsView({ container, tooltips, avatarUrl, onTimeline, onLoaded }) {
  let feed = null;
  let data = null; // last response for (feed, days)
  let error = null;
  let loading = false;
  let seq = 0;
  let timelineStart = null; // Date: the timeline has events from this day on
  const savedRange = Number(storageGet(RANGE_KEY));
  let days = RANGES.includes(savedRange) ? savedRange : 28;
  let state = freshState();
  const dayItems = new Map(); // "login|day" -> { items, error, loading } for the current data

  function freshState() {
    return {
      kinds: new Set(),
      repos: new Set(),
      sort: "total",
      focus: null,
      selected: null,
      reposOpen: false,
    };
  }

  const passes = (entry, ignore) =>
    (ignore === "kind" || !state.kinds.size || state.kinds.has(entry.kind)) &&
    (ignore === "repo" || !state.repos.size || state.repos.has(entry.repo));

  async function load({ quiet = false } = {}) {
    if (!feed) return;
    const current = ++seq;
    loading = true;
    if (!quiet || !data) {
      data = null;
      error = null;
      render();
    }
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const result = await api.feedContributions(feed.id, { days, tz });
      if (current !== seq) return;
      data = result;
      error = null;
      dayItems.clear();
    } catch (failure) {
      if (current !== seq) return;
      error = failure;
    } finally {
      if (current === seq) {
        loading = false;
        render();
      }
    }
  }

  // ----- rendering -----------------------------------------------------

  function render() {
    if (!feed) return;
    if (!data) {
      container.innerHTML = error
        ? `<div class="error-box"><strong>Could not read the contributions</strong>${escapeHtml(error.message)}</div>`
        : `<p class="gh-cg-loading">Reading ${plural(days / 7, "week")} of commits, pull requests and reviews…</p>
           ${'<div class="skeleton"></div>'.repeat(6)}`;
      onLoaded?.();
      return;
    }
    const columns = daysBetween(data.since, data.until);
    const entries = data.counts.map((count) => ({ ...count }));
    const visible = entries.filter((entry) => passes(entry));
    const people = peopleRows(columns, visible);
    const today = dayKey(new Date());

    container.innerHTML = `
      <p class="gh-cg-source">Commits that reached a default branch, pull requests and issues opened,
        reviews of other people's pull requests. Read from the repositories, so it reaches weeks back;
        pushes to other branches and comments are not counted.</p>
      ${warningsHtml()}
      <div class="gh-cg-toolbar">
        <div class="gh-cg-group"><span class="gh-cg-label">kind</span>${kindChipsHtml(entries)}</div>
        <div class="gh-cg-group"><span class="gh-cg-label">repo</span>${repoChipsHtml(entries)}</div>
        <div class="gh-cg-group">
          <span class="gh-cg-label">sort</span>
          <div class="gh-seg" role="group" aria-label="Sort contributors">
            ${[
              ["total", "volume"],
              ["recent", "last 7 d"],
              ["name", "name"],
            ]
              .map(
                ([value, label]) =>
                  `<button type="button" data-sort="${value}" aria-pressed="${state.sort === value}">${label}</button>`
              )
              .join("")}
          </div>
        </div>
      </div>
      <div class="gh-cg-summary">${summaryHtml(columns, visible, people)}</div>
      ${jumpHtml(columns)}
      <div class="gh-cg-wrap">
        <div class="gh-cg${state.focus ? " is-focus" : ""}" style="--days:${columns.length}">
          ${headHtml(columns, today)}
          ${stripHtml(columns, visible, people)}
          ${people.length ? people.map((person) => rowHtml(person, columns, people, today)).join("") : '<div class="empty empty-sm"><p>No contributions match these filters.</p></div>'}
        </div>
      </div>
      <div class="gh-cg-legend">
        <span>less</span>
        <span class="gh-cg-scale">${[0, 1, 2, 3, 4].map((level) => `<span class="gh-cg-cell lv${level}" style="--k:${cellColor()}"></span>`).join("")}</span>
        <span>more</span>
        ${timelineStart ? '<span><span class="gh-cg-cover-swatch"></span>days the timeline has events for</span>' : ""}
        <span>click a name to highlight it · click a day to open it</span>
      </div>`;
    attachTooltips(columns, visible, people, today);
    onLoaded?.();
  }

  function warningsHtml() {
    const warnings = data.warnings || [];
    if (!warnings.length) return "";
    return `<div class="gh-warnings">${warnings.map((warning) => `<div>⚠ ${escapeHtml(warning)}</div>`).join("")}</div>`;
  }

  function kindChipsHtml(entries) {
    const counts = byKind(entries.filter((entry) => passes(entry, "kind")));
    return KINDS.filter((kind) => counts.get(kind) || state.kinds.has(kind))
      .map(
        (kind) => `
          <button type="button" class="gh-kind-chip kind-${kind}${state.kinds.has(kind) ? " is-active" : ""}"
            data-kind="${kind}" aria-pressed="${state.kinds.has(kind)}">
            <span>${escapeHtml(CONTRIBUTION_LABELS[kind])}</span><b>${counts.get(kind)}</b>
          </button>`
      )
      .join("");
  }

  function repoChipsHtml(entries) {
    const counts = grouped(
      entries.filter((entry) => passes(entry, "repo")),
      (entry) => entry.repo
    );
    for (const repo of state.repos)
      if (!counts.some(([name]) => name === repo)) counts.push([repo, 0]);
    const shown = state.reposOpen
      ? counts
      : counts.filter(([repo], index) => index < REPOS_SHOWN || state.repos.has(repo));
    const hidden = counts.length - shown.length;
    return (
      shown
        .map(
          ([repo, count]) => `
          <button type="button" class="gh-kind-chip gh-cg-repo${state.repos.has(repo) ? " is-active" : ""}"
            data-repo="${escapeHtml(repo)}" aria-pressed="${state.repos.has(repo)}">
            <span>${escapeHtml(shortRepo(repo))}</span><b>${count}</b>
          </button>`
        )
        .join("") +
      (hidden || state.reposOpen
        ? `<button type="button" class="gh-people-more" data-repos-more>${state.reposOpen ? "less" : `+${hidden}`}</button>`
        : "")
    );
  }

  function peopleRows(columns, visible) {
    const index = new Map(columns.map((column, position) => [column.key, position]));
    const rows = new Map();
    for (const entry of visible) {
      const position = index.get(entry.day);
      if (position === undefined) continue;
      if (!rows.has(entry.login)) {
        rows.set(entry.login, {
          login: entry.login,
          cells: columns.map(() => []),
          total: 0,
          recent: 0,
        });
      }
      const person = rows.get(entry.login);
      person.cells[position].push(entry);
      person.total += entry.count;
      if (position >= columns.length - 7) person.recent += entry.count;
    }
    const people = [...rows.values()];
    for (const person of people) person.kinds = byKind(person.cells.flat());
    const byName = (a, b) => a.login.localeCompare(b.login, LOCALE, { sensitivity: "base" });
    people.sort((a, b) =>
      state.sort === "name"
        ? byName(a, b)
        : state.sort === "recent"
          ? b.recent - a.recent || b.total - a.total || byName(a, b)
          : b.total - a.total || byName(a, b)
    );
    return people;
  }

  function summaryHtml(columns, visible, people) {
    const total = sum(visible);
    const perDay = columns.map((column) =>
      sum(visible.filter((entry) => entry.day === column.key))
    );
    const weekdays = columns.filter((column) => !isWeekend(column.date)).length || 1;
    const busiest = perDay.reduce((best, count, index) => (count > perDay[best] ? index : best), 0);
    const parts = [
      `<span><b>${total.toLocaleString(LOCALE)}</b> ${total === 1 ? "contribution" : "contributions"}</span>`,
      `<span><b>${people.length}</b> ${people.length === 1 ? "person" : "people"}</span>`,
      `<span><b>${(total / weekdays).toFixed(1)}</b> per weekday</span>`,
    ];
    if (total) {
      parts.push(
        `<span>busiest <b>${escapeHtml(DAY_FORMAT.format(columns[busiest].date))}</b> (${perDay[busiest]})</span>`
      );
    }
    return parts.join("");
  }

  const isCovered = (key) => !timelineStart || key >= dayKey(timelineStart);

  function jumpHtml(columns) {
    if (!state.selected) return "";
    const { login, day } = state.selected;
    const column = columns.find((candidate) => candidate.key === day);
    if (!column) return "";
    const entries = data.counts.filter(
      (entry) => entry.login === login && entry.day === day && passes(entry)
    );
    const counts = byKind(entries);
    const what =
      KINDS.filter((kind) => counts.get(kind))
        .map((kind) => plural(counts.get(kind), CONTRIBUTION_LABELS[kind], PLURAL_LABELS[kind]))
        .join(" · ") || "no contributions";
    const covered = isCovered(day);
    const scope = feed.repos.length
      ? feed.repos.map((repo) => `repo:${feed.org}/${repo}`).join(" ")
      : `org:${feed.org}`;
    const search = (query, type) =>
      `https://github.com/search?q=${encodeURIComponent(`${scope} ${query}`)}&type=${type}`;
    return `
      <div class="gh-cg-jump">
        <div class="gh-cg-jump-text"><b>${escapeHtml(login)}</b> · ${escapeHtml(DAY_FORMAT.format(column.date))} · ${escapeHtml(what)}
          <span>${
            covered
              ? "The timeline has this day: it opens filtered by author and day, with every event (pushes and comments included)."
              : "Older than the events GitHub still returns for this feed; GitHub search lists that day's pull requests and commits."
          }</span>
        </div>
        <button type="button" class="btn btn-primary btn-sm" data-drill="timeline"${covered ? "" : " disabled"}>→ Timeline</button>
        <a class="btn btn-ghost btn-sm" href="${escapeHtml(search(`author:${login} created:${day}`, "pullrequests"))}" target="_blank" rel="noopener">↗ PRs</a>
        <a class="btn btn-ghost btn-sm" href="${escapeHtml(search(`author:${login} committer-date:${day}`, "commits"))}" target="_blank" rel="noopener">↗ Commits</a>
        <button type="button" class="btn btn-ghost btn-sm" data-drill="close" aria-label="Close">✕</button>
        ${dayListHtml(login, day)}
      </div>`;
  }

  // ----- the items behind a cell -------------------------------------------

  async function loadDay(login, day) {
    const key = `${login}|${day}`;
    if (dayItems.has(key)) return;
    const current = seq;
    dayItems.set(key, { loading: true });
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const items = await api.feedContributionDay(feed.id, { days, tz, login, day });
      if (current !== seq) return;
      dayItems.set(key, { items });
    } catch (failure) {
      if (current !== seq) return;
      dayItems.set(key, { error: failure.message });
    }
    if (state.selected?.login === login && state.selected?.day === day) render();
  }

  // Reviews of one PR fold into one row: replying in review threads makes
  // a "commented" review per reply, so a busy day reads as dozens of them.
  function groupedItems(items) {
    const rows = [];
    const reviews = new Map();
    for (const item of items) {
      if (item.kind !== "review") {
        rows.push({ ...item, first: item.at, states: null, count: 1 });
        continue;
      }
      const key = `${item.repo}#${item.number}`;
      let row = reviews.get(key);
      if (!row) {
        row = { ...item, url: item.url.split("#")[0], first: item.at, states: new Map(), count: 0 };
        reviews.set(key, row);
        rows.push(row);
      }
      row.count += 1;
      row.first = item.at; // items come newest first
      const state = item.review_state || "commented";
      row.states.set(state, (row.states.get(state) || 0) + 1);
    }
    return rows;
  }

  function itemText(row) {
    const ref = row.number ? `#${row.number} ` : "";
    if (row.kind === "commit") return escapeHtml(row.title || "commit");
    if (row.kind !== "review") return `opened ${escapeHtml(ref + row.title)}`;
    const states = [...row.states.entries()]
      .map(
        ([state, count]) => `${count > 1 ? `${count}× ` : ""}${REVIEW_STATE_LABELS[state] || state}`
      )
      .join(" · ");
    return `reviewed ${escapeHtml(ref + row.title)} <em>${escapeHtml(states)}</em>`;
  }

  function dayListHtml(login, day) {
    const entry = dayItems.get(`${login}|${day}`);
    if (!entry || entry.loading) return '<p class="gh-cg-items-note">Loading what they did…</p>';
    if (entry.error) {
      return `<p class="gh-cg-items-note is-error">Could not list the contributions: ${escapeHtml(entry.error)}</p>`;
    }
    const items = entry.items.filter((item) => passes(item));
    if (!items.length) return "";
    const rows = groupedItems(items)
      .map((row) => {
        const when = row.first === row.at ? hhmm(row.at) : `${hhmm(row.first)}–${hhmm(row.at)}`;
        const url = safeUrl(row.url);
        return `
          <li class="gh-cg-item">
            <span class="gh-cg-item-time">${when}</span>
            <span class="gh-cg-item-kind kind-${row.kind}">${escapeHtml(CONTRIBUTION_LABELS[row.kind])}</span>
            <span class="gh-cg-item-text">${itemText(row)}</span>
            <span class="gh-cg-item-repo">${escapeHtml(shortRepo(row.repo))}</span>
            ${url ? `<a class="gh-cg-item-link" href="${escapeHtml(url)}" target="_blank" rel="noopener" aria-label="Open on GitHub">↗</a>` : "<span></span>"}
          </li>`;
      })
      .join("");
    return `<ul class="gh-cg-items">${rows}</ul>`;
  }

  const weekClass = (column, index) => (index > 0 && column.date.getDay() === 1 ? " is-week" : "");

  function headHtml(columns, today) {
    const dense = columns.length > 28;
    const coverFrom = timelineStart
      ? columns.findIndex((column) => column.key >= dayKey(timelineStart))
      : -1;
    const cover =
      coverFrom >= 0
        ? `<div class="gh-cg-cover" style="grid-column:${coverFrom + 2} / ${columns.length + 2}" data-tooltip>timeline</div>`
        : "";
    const dates = columns
      .map((column, index) => {
        const first = index === 0 || column.date.getDate() === 1;
        const label = dense && column.date.getDay() !== 1 && !first ? "" : column.date.getDate();
        const top = first
          ? MONTH_FORMAT.format(column.date)
          : dense
            ? ""
            : WEEKDAY_FORMAT.format(column.date);
        const classes = [
          "gh-cg-date",
          isWeekend(column.date) ? "is-weekend" : "",
          column.key === today ? "is-today" : "",
          first ? "is-month" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return `<div class="${classes}${weekClass(column, index)}"><i>${escapeHtml(top)}</i><span>${label}</span></div>`;
      })
      .join("");
    return `
      <div class="gh-cg-row gh-cg-covers"><div></div>${cover}</div>
      <div class="gh-cg-row gh-cg-dates"><div></div>${dates}<div></div></div>`;
  }

  function stripHtml(columns, visible, people) {
    const perDay = columns.map((column) => visible.filter((entry) => entry.day === column.key));
    const peak = Math.max(1, ...perDay.map(sum));
    const focused = state.focus && people.find((person) => person.login === state.focus);
    const bars = columns
      .map((column, index) => {
        const counts = byKind(perDay[index]);
        const segments = KINDS.filter((kind) => counts.get(kind))
          .map(
            (kind) =>
              `<div class="gh-cg-seg kind-${kind}" style="height:${(counts.get(kind) / peak) * 100}%"></div>`
          )
          .join("");
        const mine = focused ? sum(focused.cells[index]) : 0;
        return `<div class="gh-cg-bar${weekClass(column, index)}" data-team="${index}">${segments}<div class="gh-cg-mine" style="height:${(mine / peak) * 100}%"></div></div>`;
      })
      .join("");
    return `
      <div class="gh-cg-row gh-cg-strip">
        <div class="gh-cg-strip-label">team / day</div>${bars}
        <div class="gh-cg-strip-total">peak ${peak}</div>
      </div>`;
  }

  function cellColor() {
    return state.kinds.size === 1 ? `var(--kind-${[...state.kinds][0]})` : "var(--accent)";
  }

  function rowHtml(person, columns, people, today) {
    const peak = Math.max(1, ...people.flatMap((row) => row.cells.map(sum)));
    const level = (count) =>
      count === 0 ? 0 : Math.max(1, Math.min(4, Math.ceil(4 * Math.sqrt(count / peak))));
    const viewer = (data.viewer || "").toLowerCase();
    const isYou = viewer && person.login.toLowerCase() === viewer;
    const avatar = data.avatars?.[person.login]
      ? `<img class="gh-person-avatar" src="${escapeHtml(avatarUrl(data.avatars[person.login]))}" alt="" loading="lazy" />`
      : `<span class="gh-person-avatar">${escapeHtml(person.login.slice(0, 1).toUpperCase())}</span>`;
    const cells = columns
      .map((column, index) => {
        const count = sum(person.cells[index]);
        const selected =
          state.selected?.login === person.login && state.selected?.day === column.key;
        const classes = [
          "gh-cg-cell",
          `lv${level(count)}`,
          isWeekend(column.date) ? "is-weekend" : "",
          column.key === today ? "is-today" : "",
          selected ? "is-selected" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return `<button type="button" class="${classes}${weekClass(column, index)}" style="--k:${cellColor()}"
          data-cell="${index}" aria-label="${escapeHtml(`${person.login}, ${DAY_FORMAT.format(column.date)}: ${count}`)}"></button>`;
      })
      .join("");
    const mix = KINDS.filter((kind) => person.kinds.get(kind))
      .map(
        (kind) =>
          `<i class="kind-${kind}" style="width:${(person.kinds.get(kind) / person.total) * 100}%"></i>`
      )
      .join("");
    return `
      <div class="gh-cg-row gh-cg-person${state.focus === person.login ? " is-focused" : ""}" data-login="${escapeHtml(person.login)}">
        <button type="button" class="gh-cg-who${isYou ? " is-you" : ""}" data-focus
          aria-pressed="${state.focus === person.login}">
          ${avatar}<span class="gh-cg-login" data-tip="${escapeHtml(person.login)}">${escapeHtml(person.login)}</span>
        </button>
        ${cells}
        <div class="gh-cg-total" data-mix tabindex="0" aria-label="${escapeHtml(
          `${person.total} contributions: ${KINDS.map((kind) => `${person.kinds.get(kind)} ${CONTRIBUTION_LABELS[kind]}`).join(", ")}`
        )}">
          <b>${person.total}</b><span class="gh-cg-mix">${mix}</span>
        </div>
      </div>`;
  }

  // ----- tooltips --------------------------------------------------------

  function attachTooltips(columns, visible, people, today) {
    const byLogin = new Map(people.map((person) => [person.login, person]));
    container.querySelectorAll(".gh-cg-person").forEach((row) => {
      const person = byLogin.get(row.dataset.login);
      row.querySelectorAll("[data-cell]").forEach((cell) => {
        const index = Number(cell.dataset.cell);
        tooltips.set(cell, () => cellTooltip(person, columns[index], person.cells[index], today));
      });
      tooltips.set(row.querySelector("[data-mix]"), () => mixTooltip(person, columns));
    });
    container.querySelectorAll("[data-team]").forEach((bar) => {
      const column = columns[Number(bar.dataset.team)];
      tooltips.set(bar, () =>
        teamTooltip(
          column,
          visible.filter((entry) => entry.day === column.key)
        )
      );
    });
    const cover = container.querySelector(".gh-cg-cover");
    if (cover) {
      tooltips.set(
        cover,
        () =>
          '<div class="gh-tooltip-main">Timeline coverage</div>' +
          '<div class="gh-tooltip-sub">The events API still returns these days, so the timeline can show every event of a person\'s day, comments and pushes to any branch included.</div>'
      );
    }
  }

  function cellTooltip(person, column, entries, today) {
    const total = sum(entries);
    const note =
      column.key === today
        ? "today, still counting"
        : isCovered(column.key)
          ? "click to open this day"
          : "before the timeline's events · click for GitHub search";
    return (
      `<div class="gh-tooltip-main">${escapeHtml(person.login)} · ${escapeHtml(DAY_FORMAT.format(column.date))}</div>` +
      `<div class="gh-tooltip-sub">${plural(total, "contribution")}</div>` +
      (total
        ? `<div class="gh-tip-kinds">${kindChips(byKind(entries))}</div>` +
          rowsTable(
            grouped(entries, (entry) => shortRepo(entry.repo)),
            total
          )
        : "") +
      `<div class="gh-tooltip-sub gh-tip-hint">${note}</div>`
    );
  }

  function mixTooltip(person, columns) {
    const rows = KINDS.filter((kind) => person.kinds.get(kind))
      .map((kind) => {
        const count = person.kinds.get(kind);
        const share = Math.round((count / person.total) * 100);
        return `
          <span class="gh-cg-tip-kind kind-${kind}"><i></i>${escapeHtml(CONTRIBUTION_LABELS[kind])}</span>
          <b>${count}</b><em>${share}%</em>`;
      })
      .join("");
    const activeDays = person.cells.filter((cell) => cell.length).length;
    const repos = grouped(person.cells.flat(), (entry) => shortRepo(entry.repo));
    const repoLine = repos
      .slice(0, 3)
      .map(([repo]) => repo)
      .join(", ");
    return (
      `<div class="gh-tooltip-main">${escapeHtml(person.login)}</div>` +
      `<div class="gh-tooltip-sub">${plural(person.total, "contribution")} in ${plural(columns.length, "day")} · active on ${activeDays}</div>` +
      `<div class="gh-cg-tip-mix">${rows}</div>` +
      `<div class="gh-tooltip-sub gh-tip-repos">${escapeHtml(repoLine)}${repos.length > 3 ? ` +${repos.length - 3}` : ""}</div>`
    );
  }

  function teamTooltip(column, entries) {
    const total = sum(entries);
    return (
      `<div class="gh-tooltip-main">${escapeHtml(DAY_FORMAT.format(column.date))}</div>` +
      `<div class="gh-tooltip-sub">${plural(total, "contribution")}</div>` +
      (total
        ? `<div class="gh-tip-kinds">${kindChips(byKind(entries))}</div>` +
          rowsTable(
            grouped(entries, (entry) => entry.login),
            total
          )
        : "")
    );
  }

  // ----- interaction -----------------------------------------------------

  container.addEventListener("click", (event) => {
    if (!data) return;
    const target = event.target;
    const kind = target.closest("[data-kind]");
    if (kind) return toggle(state.kinds, kind.dataset.kind);
    const repo = target.closest("[data-repo]");
    if (repo) return toggle(state.repos, repo.dataset.repo);
    if (target.closest("[data-repos-more]")) {
      state.reposOpen = !state.reposOpen;
      return render();
    }
    const sort = target.closest("[data-sort]");
    if (sort) {
      state.sort = sort.dataset.sort;
      return render();
    }
    const row = target.closest(".gh-cg-person");
    if (target.closest("[data-focus]") && row) {
      state.focus = state.focus === row.dataset.login ? null : row.dataset.login;
      return render();
    }
    const cell = target.closest("[data-cell]");
    if (cell && row) {
      const day = daysBetween(data.since, data.until)[Number(cell.dataset.cell)].key;
      const same = state.selected?.login === row.dataset.login && state.selected?.day === day;
      state.selected = same ? null : { login: row.dataset.login, day };
      render();
      if (state.selected) loadDay(state.selected.login, day);
      return;
    }
    const drill = target.closest("[data-drill]");
    if (drill) {
      const selected = state.selected;
      state.selected = null;
      render();
      if (drill.dataset.drill === "timeline" && selected) onTimeline(selected);
    }
  });

  container.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && (state.selected || state.focus)) {
      state.selected = null;
      state.focus = null;
      render();
    }
  });

  function toggle(set, value) {
    if (set.has(value)) set.delete(value);
    else set.add(value);
    render();
  }

  return {
    /** Show a feed's contributions (state resets when the feed changes). */
    open(nextFeed) {
      const changed = feed?.id !== nextFeed.id;
      feed = nextFeed;
      if (changed) {
        state = freshState();
        data = null;
        error = null;
      }
      if (changed || (!data && !loading)) load();
      else render();
    },
    refresh: () => load({ quiet: true }),
    close() {
      feed = null;
      data = null;
      seq++;
    },
    get days() {
      return days;
    },
    setDays(next) {
      if (!RANGES.includes(next) || next === days) return;
      days = next;
      storageSet(RANGE_KEY, String(next));
      state.selected = null;
      load();
    },
    /** The first day the timeline has events for (null: unknown). */
    setTimelineStart(date) {
      timelineStart = date;
      if (feed && data) render();
    },
    get busy() {
      return loading;
    },
    meta() {
      if (!data) return null;
      const total = sum(data.counts);
      return {
        total,
        fetched_at: data.fetched_at,
        rate_remaining: data.rate_remaining,
        rate_limit: data.rate_limit,
      };
    },
  };
}
