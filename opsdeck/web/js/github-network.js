// The "network" mode of the GitHub tab: the feed's people linked to the
// repositories they contributed to in the period, read from the same daily
// contributions as the contributors grid. The layout is bipartite: repos are
// the anchors (size = activity), each person settles next to the repository
// most of their work went to, a link's width is how much they did there and
// its length how large a share of their (or the repo's) work it was. People
// linked to two or more repositories are "bridges"; a repository where one
// person made most of the work is "concentrated". Kind and repo chips are
// client-side facets, like the grid's. The physics is a small simulation in
// the spirit of d3-force (velocity Verlet, alpha cooling), enough for a
// feed's people without a dependency.

import { api } from "./api.js";
import { CONTRIBUTION_LABELS } from "./github-contributors.js";
import { escapeHtml, LOCALE } from "./ui.js";

const KINDS = Object.keys(CONTRIBUTION_LABELS);
const PLURAL_LABELS = { commit: "commits", pr: "PRs", review: "reviews", issue: "issues" };
const RANGES = [14, 28, 56];
const RANGE_KEY = "opsdeck.github.range";
const SHARE_KEY = "opsdeck.github.network.share";
const LENGTH_KEY = "opsdeck.github.network.length";
const REPOS_SHOWN = 8;
const DEFAULT_MIN_SHARE = 5; // % of a person's work a link needs to be drawn
const CONCENTRATED = 0.6; // share of a repo's work that makes one person its bus factor
const LABELED_PEOPLE = 8; // the busiest people carry a label at rest; the others when lit
const SCALE_MIN = 0.4;
const SCALE_MAX = 3;
const CLICK_DISTANCE = 4; // px: a shorter pointer travel is a click, not a drag
const GITHUB_WEB = "https://github.com/";
const SVG_NS = "http://www.w3.org/2000/svg";

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
const pct = (share) => `${Math.round(share * 100)} %`;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const personRadius = (total) => clamp(9 + 1.35 * Math.sqrt(total), 10, 22);
const repoRadius = (total) => clamp(15 + 2.5 * Math.sqrt(total), 22, 54);
const linkWidth = (count) => clamp(1 + 0.85 * Math.sqrt(count), 1, 9);

function svgElement(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}

// One ring segment from angle a0 to a1 (radians, clockwise from 12 o'clock).
function arcPath(inner, outer, a0, a1) {
  const point = (radius, angle) =>
    `${(radius * Math.sin(angle)).toFixed(2)} ${(-radius * Math.cos(angle)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return (
    `M${point(outer, a0)}A${outer} ${outer} 0 ${large} 1 ${point(outer, a1)}` +
    `L${point(inner, a1)}A${inner} ${inner} 0 ${large} 0 ${point(inner, a0)}Z`
  );
}

function kindChips(counts) {
  return `<div class="gh-tip-kinds">${KINDS.filter((kind) => counts.get(kind))
    .map(
      (kind) =>
        `<span class="gh-tip-kind kind-${kind}">${escapeHtml(CONTRIBUTION_LABELS[kind])} ${counts.get(kind)}</span>`
    )
    .join("")}</div>`;
}

function mixHtml(counts, total) {
  if (!total) return "";
  const bar = KINDS.filter((kind) => counts.get(kind))
    .map(
      (kind) => `<i class="kind-${kind}" style="width:${(counts.get(kind) / total) * 100}%"></i>`
    )
    .join("");
  const legend = KINDS.filter((kind) => counts.get(kind))
    .map(
      (kind) =>
        `<span class="kind-${kind}"><i></i>${escapeHtml(PLURAL_LABELS[kind])} <b>${counts.get(kind)}</b></span>`
    )
    .join("");
  return `<div class="gh-net-mix">${bar}</div><div class="gh-net-mix-legend">${legend}</div>`;
}

// ----- physics ---------------------------------------------------------

// Velocity Verlet with alpha cooling, as d3-force does it: links are springs
// with their own rest length, every node repels every other (a feed has tens
// of nodes, so the plain O(n²) is fine), nodes do not overlap, and a gentle
// gravity keeps the drawing centered, stronger on the canvas' short axis.
function createSimulation(nodes, links, { center, onTick, onEnd }) {
  const ALPHA_MIN = 0.001;
  const ALPHA_DECAY = 0.03;
  const VELOCITY_DECAY = 0.4;
  const DISTANCE_MAX2 = 520 * 520;
  let alpha = 1;
  let alphaTarget = 0;
  let frame = null;
  const jiggle = () => (Math.random() - 0.5) * 1e-6;
  const degree = new Map();
  for (const link of links) {
    degree.set(link.source, (degree.get(link.source) || 0) + 1);
    degree.set(link.target, (degree.get(link.target) || 0) + 1);
  }
  for (const link of links) {
    const source = degree.get(link.source);
    link.bias = source / (source + degree.get(link.target));
  }

  function forceLinks() {
    for (const { source, target, distance, strength, bias } of links) {
      let dx = target.x + target.vx - source.x - source.vx || jiggle();
      let dy = target.y + target.vy - source.y - source.vy || jiggle();
      let length = Math.hypot(dx, dy);
      length = ((length - distance) / length) * alpha * strength;
      dx *= length;
      dy *= length;
      target.vx -= dx * bias;
      target.vy -= dy * bias;
      source.vx += dx * (1 - bias);
      source.vy += dy * (1 - bias);
    }
  }

  function forceCharge() {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = b.x - a.x || jiggle();
        const dy = b.y - a.y || jiggle();
        const l2 = Math.max(dx * dx + dy * dy, 25);
        if (l2 > DISTANCE_MAX2) continue;
        const onB = (a.charge * alpha) / l2; // a's charge, felt by b (negative: away)
        const onA = (b.charge * alpha) / l2;
        b.vx -= dx * onB;
        b.vy -= dy * onB;
        a.vx += dx * onA;
        a.vy += dy * onA;
      }
    }
  }

  function forceCollide() {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const gap = a.space + b.space;
        let dx = b.x + b.vx - a.x - a.vx || jiggle();
        let dy = b.y + b.vy - a.y - a.vy || jiggle();
        const length = Math.hypot(dx, dy);
        if (length >= gap) continue;
        const push = ((gap - length) / length) * 0.9;
        dx *= push;
        dy *= push;
        const share = (b.space * b.space) / (a.space * a.space + b.space * b.space);
        b.vx += dx * share;
        b.vy += dy * share;
        a.vx -= dx * (1 - share);
        a.vy -= dy * (1 - share);
      }
    }
  }

  function forceGravity() {
    for (const node of nodes) {
      node.vx += (center.x - node.x) * node.gravity * alpha;
      node.vy += (center.y - node.y) * node.gravity * center.aspect * alpha;
    }
  }

  function tick() {
    alpha += (alphaTarget - alpha) * ALPHA_DECAY;
    forceLinks();
    forceCharge();
    forceCollide();
    forceGravity();
    for (const node of nodes) {
      if (node.fx == null) {
        node.vx *= 1 - VELOCITY_DECAY;
        node.x += node.vx;
      } else {
        node.x = node.fx;
        node.vx = 0;
      }
      if (node.fy == null) {
        node.vy *= 1 - VELOCITY_DECAY;
        node.y += node.vy;
      } else {
        node.y = node.fy;
        node.vy = 0;
      }
    }
  }

  function loop() {
    tick();
    onTick();
    if (alpha < ALPHA_MIN) {
      frame = null;
      onEnd();
      return;
    }
    frame = requestAnimationFrame(loop);
  }

  return {
    /** Warm the simulation up to `value` and keep animating until it cools. */
    restart(value) {
      alpha = Math.max(alpha, value);
      if (frame === null) frame = requestAnimationFrame(loop);
    },
    /** Run `count` steps at once, drawing only the result. */
    settle(count) {
      for (let i = 0; i < count; i++) tick();
      onTick();
    },
    /** While dragging the simulation idles at `value` instead of cooling. */
    hold(value) {
      alphaTarget = value;
      if (value > 0) this.restart(value);
    },
    stop() {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    },
  };
}

/**
 * @param {object} deps
 * @param {HTMLElement} deps.container where the view renders
 * @param {WeakMap<Element, () => string>} deps.tooltips rich tooltip builders (github.js)
 * @param {(url: string) => string} deps.avatarUrl sized avatar URL
 * @param {(refinements: {field: string, value: string}[]) => void} deps.onTimeline open the timeline refined by a person or a repo
 * @param {(login: string) => void} deps.onContributors open the contributors grid on a person
 * @param {() => void} deps.onLoaded called after each render (meta line refresh)
 * @param {(busy: boolean) => void} deps.onBusy a read of the contributions started or ended
 * @param {() => void} deps.hideTooltip the floating tooltip must not chase a dragged node
 */
export function createNetworkView({
  container,
  tooltips,
  avatarUrl,
  onTimeline,
  onContributors,
  onLoaded,
  onBusy,
  hideTooltip,
}) {
  let feed = null;
  let data = null; // last response for (feed, days)
  let error = null;
  let loading = false;
  let seq = 0;
  let elapsedTimer = null;
  const savedRange = Number(storageGet(RANGE_KEY));
  let days = RANGES.includes(savedRange) ? savedRange : 28;
  const savedShare = Number(storageGet(SHARE_KEY));
  let minShare =
    Number.isInteger(savedShare) && storageGet(SHARE_KEY) !== null ? savedShare : DEFAULT_MIN_SHARE;
  let lengthBy = storageGet(LENGTH_KEY) === "repo" ? "repo" : "person";
  let state = freshState();
  // node objects of the current feed, kept so positions (and pins) survive
  // re-renders: filters, the threshold, the period
  let placed = new Map();
  let graph = null; // what the canvas shows: nodes, links and their elements
  let scene = null; // the svg, its layers and the view transform
  let sim = null;
  let resizeObserver = null;

  function freshState() {
    return {
      kinds: new Set(),
      repos: new Set(),
      reposOpen: false,
      hover: null, // {kind: "node" | "link", id} under the pointer
      rowFocus: null, // node id lit from a panel row or a repo chip
      selected: null, // pinned node id
    };
  }

  const passes = (entry, ignore) =>
    (ignore === "kind" || !state.kinds.size || state.kinds.has(entry.kind)) &&
    (ignore === "repo" || !state.repos.size || state.repos.has(entry.repo));

  async function load({ quiet = false } = {}) {
    if (!feed) return;
    const current = ++seq;
    loading = true;
    onBusy?.(true);
    const started = Date.now();
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      const label = container.querySelector("[data-elapsed]");
      if (label) label.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
    }, 1000);
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
    } catch (failure) {
      if (current !== seq) return;
      error = failure;
    } finally {
      if (current === seq) {
        loading = false;
        clearInterval(elapsedTimer);
        onBusy?.(false);
        render();
      }
    }
  }

  // Drop the data (and a read in flight) without rendering: the next open reads again.
  function forget() {
    data = null;
    seq++;
    if (loading) {
      loading = false;
      clearInterval(elapsedTimer);
      onBusy?.(false);
    }
  }

  // ----- the graph behind the drawing --------------------------------------

  function buildGraph() {
    const entries = data.counts.filter((entry) => passes(entry));
    const people = new Map(); // login -> {total, kinds, repos: Map(repo -> {count, kinds})}
    const activity = new Map(); // repo -> contributions in the period
    for (const entry of entries) {
      let person = people.get(entry.login);
      if (!person) {
        person = { total: 0, kinds: new Map(), repos: new Map() };
        people.set(entry.login, person);
      }
      person.total += entry.count;
      person.kinds.set(entry.kind, (person.kinds.get(entry.kind) || 0) + entry.count);
      let work = person.repos.get(entry.repo);
      if (!work) {
        work = { count: 0, kinds: new Map() };
        person.repos.set(entry.repo, work);
      }
      work.count += entry.count;
      work.kinds.set(entry.kind, (work.kinds.get(entry.kind) || 0) + entry.count);
      activity.set(entry.repo, (activity.get(entry.repo) || 0) + entry.count);
    }
    const repos = new Map();
    const repoNode = (id) => {
      let node = repos.get(id);
      if (!node) {
        node = { id, type: "repo", total: 0, kinds: new Map(), links: [] };
        repos.set(id, node);
      }
      return node;
    };
    // repositories the feed lists stay on the canvas even when nothing landed
    // in them: an empty node says so
    for (const name of feed.repos) {
      const id = `${feed.org}/${name}`;
      if (!state.repos.size || state.repos.has(id)) repoNode(id);
    }
    const links = [];
    const nodes = [];
    let hiddenPeople = 0;
    for (const [login, person] of people) {
      // a person's link to a repo is drawn when it holds at least minShare of their work
      const kept = [...person.repos].filter(
        ([, work]) => work.count / person.total >= minShare / 100
      );
      if (!kept.length) {
        hiddenPeople += 1;
        continue;
      }
      const node = {
        id: login,
        type: "person",
        total: person.total,
        kinds: person.kinds,
        links: [],
        r: personRadius(person.total),
      };
      for (const [repo, work] of kept) {
        const target = repoNode(repo);
        target.total += work.count;
        for (const [kind, count] of work.kinds)
          target.kinds.set(kind, (target.kinds.get(kind) || 0) + count);
        const link = {
          id: `${login}|${repo}`,
          login,
          repo,
          count: work.count,
          kinds: work.kinds,
          personShare: work.count / person.total,
        };
        links.push(link);
        node.links.push(link);
        target.links.push(link);
      }
      node.links.sort((a, b) => b.count - a.count);
      node.bridge = node.links.length >= 2;
      nodes.push(node);
    }
    for (const repo of repos.values()) {
      repo.r = repo.total ? repoRadius(repo.total) : 18;
      repo.links.sort((a, b) => b.count - a.count);
      for (const link of repo.links) link.repoShare = link.count / repo.total;
      repo.top = repo.links[0] || null;
      repo.concentrated = repo.total >= 5 && repo.top && repo.top.repoShare >= CONCENTRATED;
      nodes.push(repo);
    }
    return { nodes, links, hiddenPeople, entries, activity };
  }

  // ----- rendering -----------------------------------------------------

  function render() {
    if (!feed) return;
    teardownScene();
    if (!data) {
      container.innerHTML = error
        ? `<div class="error-box"><strong>Could not read the contributions</strong>${escapeHtml(error.message)}</div>`
        : skeletonHtml();
      onLoaded?.();
      return;
    }
    const entries = data.counts.map((count) => ({ ...count }));
    container.innerHTML = `
      <p class="gh-cg-source">Each link joins a person to a repository they contributed to in the period
        (the same commits, pull requests, issues and reviews the contributors grid counts). Its width is how much
        they did there and its length how large a share of their work it was, so people settle next to the
        repository they live in. People linked to two or more repositories are <b>bridges</b>; a repository where
        one person made ${Math.round(CONCENTRATED * 100)} % or more of the work is <b>concentrated</b>.</p>
      ${warningsHtml()}
      <div class="gh-cg-toolbar">
        <div class="gh-cg-group"><span class="gh-cg-label">kind</span>${kindChipsHtml(entries)}</div>
        <div class="gh-cg-group"><span class="gh-cg-label">repo</span>${repoChipsHtml(entries)}</div>
        <label class="gh-net-range">
          <span class="gh-cg-label">hide links under</span>
          <input type="range" min="0" max="30" step="1" value="${minShare}" data-share aria-label="Smallest share of a person's work a link is drawn for">
          <b data-share-value>${minShare} %</b><span>of a person's work</span>
        </label>
        <div class="gh-cg-group">
          <span class="gh-cg-label">length</span>
          <div class="gh-seg" role="group" aria-label="What a link's length measures">
            <button type="button" data-length="person" aria-pressed="${lengthBy === "person"}" title="A short link is a large share of the person's work">person's share</button>
            <button type="button" data-length="repo" aria-pressed="${lengthBy === "repo"}" title="A short link is a large share of the repository's work">repo's share</button>
          </div>
        </div>
      </div>
      <p class="gh-net-status" data-status></p>
      <div class="gh-net-stage">
        <div class="gh-net-canvas" data-canvas>
          <svg class="gh-net-svg" tabindex="0" role="img" aria-label="Contributor network"></svg>
          <div class="gh-net-ctl">
            <button type="button" data-zoom="in" title="Zoom in" aria-label="Zoom in">+</button>
            <button type="button" data-zoom="out" title="Zoom out" aria-label="Zoom out">−</button>
            <button type="button" data-zoom="fit" title="Fit everything" aria-label="Fit everything">⌖</button>
          </div>
          <div class="gh-net-legend" aria-hidden="true">
            <span><i class="gh-net-lg-repo"></i>repository · size = activity</span>
            <span><i class="gh-net-lg-person"></i>person</span>
            <span><i class="gh-net-lg-ring"></i>ring = kind mix</span>
            <span><i class="gh-net-lg-link"></i>width = contributions</span>
            <span><i class="gh-net-lg-bridge"></i>bridge</span>
          </div>
          <div class="gh-net-hint" aria-hidden="true">drag to move · double-click to release · ctrl + scroll to zoom</div>
        </div>
        <aside class="gh-net-panel" data-panel aria-live="polite"></aside>
      </div>`;
    renderGraph();
    onLoaded?.();
  }

  // Rebuilds the drawing, the status line and the panel for the current
  // filters; the toolbar stays, so a slider being dragged is not destroyed.
  function renderGraph() {
    teardownScene();
    graph = buildGraph();
    renderStatus();
    buildScene();
    renderPanel();
    applyFocus();
  }

  function skeletonHtml() {
    const blobs = [
      [28, 30, 46],
      [64, 26, 34],
      [48, 62, 40],
      [78, 66, 28],
      [18, 70, 22],
      [40, 44, 12],
      [58, 48, 12],
      [70, 42, 12],
      [34, 24, 12],
      [86, 50, 12],
    ]
      .map(
        ([x, y, size], index) =>
          `<i class="gh-net-skel-blob" style="--x:${x}%;--y:${y}%;--s:${size}px;--d:${index}"></i>`
      )
      .join("");
    return `
      <p class="gh-cg-loading" role="status">
        Reading ${plural(days / 7, "week")} of commits, pull requests and reviews
        <b data-elapsed>0 s</b>
      </p>
      <div class="gh-net-stage">
        <div class="gh-net-canvas gh-net-skeleton">${blobs}</div>
        <aside class="gh-net-panel">
          <div class="gh-skel gh-net-skel-line" style="--w:40%"></div>
          <div class="gh-net-skel-stats">${'<span class="gh-skel"></span>'.repeat(4)}</div>
          <div class="gh-skel gh-net-skel-line" style="--w:55%"></div>
          <div class="gh-skel gh-net-skel-line" style="--w:70%"></div>
          <div class="gh-skel gh-net-skel-line" style="--w:62%"></div>
        </aside>
      </div>`;
  }

  function warningsHtml() {
    const warnings = data.warnings || [];
    if (!warnings.length) return "";
    return `<div class="gh-warnings">${warnings.map((warning) => `<div>⚠ ${escapeHtml(warning)}</div>`).join("")}</div>`;
  }

  function byKind(entries) {
    const counts = new Map(KINDS.map((kind) => [kind, 0]));
    for (const entry of entries)
      counts.set(entry.kind, (counts.get(entry.kind) || 0) + entry.count);
    return counts;
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
    const counts = new Map();
    for (const entry of entries.filter((entry) => passes(entry, "repo")))
      counts.set(entry.repo, (counts.get(entry.repo) || 0) + entry.count);
    const rows = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    for (const repo of state.repos) if (!counts.has(repo)) rows.push([repo, 0]);
    const shown = state.reposOpen
      ? rows
      : rows.filter(([repo], index) => index < REPOS_SHOWN || state.repos.has(repo));
    const hidden = rows.length - shown.length;
    return (
      shown
        .map(
          ([repo, count]) => `
          <button type="button" class="gh-kind-chip gh-cg-repo${state.repos.has(repo) ? " is-active" : ""}"
            data-repo="${escapeHtml(repo)}" data-node-hover="${escapeHtml(repo)}" aria-pressed="${state.repos.has(repo)}">
            <span>${escapeHtml(shortRepo(repo))}</span><b>${count}</b>
          </button>`
        )
        .join("") +
      (hidden || state.reposOpen
        ? `<button type="button" class="gh-people-more" data-repos-more>${state.reposOpen ? "less" : `+${hidden}`}</button>`
        : "")
    );
  }

  function renderStatus() {
    const people = graph.nodes.filter((node) => node.type === "person");
    const repos = graph.nodes.filter((node) => node.type === "repo" && node.total);
    const bridges = people.filter((node) => node.bridge).length;
    const parts = [
      `<b>${people.length}</b> ${people.length === 1 ? "person" : "people"}`,
      `<b>${repos.length}</b> active ${repos.length === 1 ? "repo" : "repos"}`,
      `<b>${graph.links.length}</b> ${graph.links.length === 1 ? "link" : "links"}`,
      `<b>${bridges}</b> ${bridges === 1 ? "bridge" : "bridges"}`,
    ];
    if (graph.hiddenPeople) {
      parts.push(
        `${graph.hiddenPeople} ${graph.hiddenPeople === 1 ? "person" : "people"} below ${minShare} %`
      );
    }
    container.querySelector("[data-status]").innerHTML = parts.join(" · ");
  }

  // ----- the drawing -----------------------------------------------------

  function teardownScene() {
    sim?.stop();
    sim = null;
    resizeObserver?.disconnect();
    resizeObserver = null;
    scene = null;
  }

  // Repos start on a ring, people next to the repo they contribute to most;
  // nodes seen before keep their place.
  function place(node, index, repoCount, width, height) {
    const previous = placed.get(node.id);
    if (previous) {
      Object.assign(node, {
        x: previous.x,
        y: previous.y,
        vx: 0,
        vy: 0,
        fx: previous.fx,
        fy: previous.fy,
        pinned: previous.pinned,
      });
      return;
    }
    if (node.type === "repo") {
      const angle = (index / Math.max(1, repoCount)) * Math.PI * 2 - Math.PI / 2;
      const radius = Math.min(width, height) * 0.3;
      node.x = width / 2 + Math.cos(angle) * radius;
      node.y = height / 2 + Math.sin(angle) * radius;
    } else {
      const home = placed.get(node.links[0]?.repo) || { x: width / 2, y: height / 2 };
      const angle = Math.random() * Math.PI * 2;
      node.x = home.x + Math.cos(angle) * 60;
      node.y = home.y + Math.sin(angle) * 60;
    }
    node.vx = 0;
    node.vy = 0;
    node.fx = null;
    node.fy = null;
    node.pinned = false;
  }

  function buildScene() {
    const canvas = container.querySelector("[data-canvas]");
    const svg = canvas.querySelector("svg");
    const width = canvas.clientWidth || 800;
    const height = canvas.clientHeight || 480;
    svg.innerHTML = "";
    const empty = canvas.querySelector(".gh-net-empty");
    empty?.remove();
    if (!graph.nodes.length) {
      canvas.insertAdjacentHTML(
        "beforeend",
        '<div class="gh-net-empty"><div class="empty empty-sm"><p>No contributions match these filters.</p></div></div>'
      );
      return;
    }
    const viewport = svgElement("g", { class: "gh-net-viewport" });
    const linkLayer = svgElement("g");
    const nodeLayer = svgElement("g");
    const labelLayer = svgElement("g");
    viewport.append(linkLayer, nodeLayer, labelLayer);
    svg.append(viewport);
    const byId = new Map(graph.nodes.map((node) => [node.id, node]));

    const repoCount = graph.nodes.filter((node) => node.type === "repo").length;
    let repoIndex = 0;
    for (const node of graph.nodes) {
      if (node.type === "repo") place(node, repoIndex++, repoCount, width, height);
    }
    const nextPlaced = new Map(placed);
    for (const node of graph.nodes) if (node.type === "repo") nextPlaced.set(node.id, node);
    placed = nextPlaced;
    for (const node of graph.nodes) {
      if (node.type === "person") {
        place(node, 0, repoCount, width, height);
        placed.set(node.id, node);
      }
      node.charge = node.type === "repo" ? -1100 : -220;
      node.space = node.r + (node.type === "repo" ? 18 : 12);
      node.gravity = node.type === "repo" ? 0.05 : 0.025;
    }
    for (const link of graph.links) {
      link.source = byId.get(link.login);
      link.target = byId.get(link.repo);
      const share = lengthBy === "person" ? link.personShare : link.repoShare;
      link.distance = link.source.r + link.target.r + 36 + (1 - share) * 210;
      link.strength = 0.2 + 0.8 * share;
      link.element = svgElement("g", { class: "gh-net-link", "data-tooltip": "" });
      link.hit = svgElement("line", {
        class: "gh-net-link-hit",
        "stroke-width": Math.max(14, linkWidth(link.count) + 10),
      });
      link.line = svgElement("line", {
        class: "gh-net-link-line",
        "stroke-width": linkWidth(link.count).toFixed(2),
      });
      link.element.append(link.hit, link.line);
      linkLayer.append(link.element);
      tooltips.set(link.element, () => linkTooltip(link));
      link.element.addEventListener("pointerenter", () => setHover({ kind: "link", id: link.id }));
      link.element.addEventListener("pointerleave", () => setHover(null));
    }

    const busiest = new Set(
      graph.nodes
        .filter((node) => node.type === "person")
        .sort((a, b) => b.total - a.total)
        .slice(0, LABELED_PEOPLE)
        .map((node) => node.id)
    );
    const viewer = (data.viewer || "").toLowerCase();
    for (const node of graph.nodes) {
      const classes = [
        "gh-net-node",
        node.type === "repo" ? "is-repo" : "is-person",
        node.type === "repo" && !node.total ? "is-empty" : "",
        node.type === "person" && node.id.toLowerCase() === viewer ? "is-you" : "",
      ]
        .filter(Boolean)
        .join(" ");
      node.element = svgElement("g", { class: classes, "data-tooltip": "" });
      const halo = svgElement("circle", { class: "gh-net-halo", r: node.r + 8 });
      if (!node.bridge) halo.setAttribute("display", "none");
      node.selection = svgElement("circle", {
        class: "gh-net-sel",
        r: node.r + 8,
        display: "none",
      });
      const body = svgElement("circle", { class: "gh-net-body", r: node.r });
      node.element.append(halo, node.selection, body, ringGroup(node));
      if (node.type === "repo") {
        const text = svgElement("text", { class: "gh-net-text" });
        text.textContent = node.total ? String(node.total) : "–";
        node.element.append(text);
      } else {
        const avatar = data.avatars?.[node.id];
        if (avatar) {
          const image = svgElement("image", {
            class: "gh-net-avatar",
            href: avatarUrl(avatar),
            x: -node.r + 1.5,
            y: -node.r + 1.5,
            width: node.r * 2 - 3,
            height: node.r * 2 - 3,
          });
          node.element.append(image);
        } else {
          const text = svgElement("text", { class: "gh-net-text" });
          text.textContent = node.id.slice(0, 2).toUpperCase();
          node.element.append(text);
        }
      }
      node.pin = svgElement("circle", {
        class: "gh-net-pin",
        r: 3,
        cx: node.r * 0.72,
        cy: -node.r * 0.72,
      });
      if (!node.pinned) node.pin.setAttribute("display", "none");
      node.element.append(node.pin, svgElement("circle", { class: "gh-net-hit", r: node.r + 10 }));
      nodeLayer.append(node.element);
      tooltips.set(node.element, () => nodeTooltip(node));

      node.label = svgElement("text", {
        class: `gh-net-label ${node.type === "repo" ? "is-repo" : "is-person"}`,
        dy: node.r + (node.type === "repo" ? 16 : 13),
      });
      node.label.textContent = node.type === "repo" ? shortRepo(node.id) : node.id;
      if (node.type === "person" && !busiest.has(node.id)) node.label.classList.add("is-quiet");
      labelLayer.append(node.label);
      attachNodePointer(node);
    }

    scene = {
      canvas,
      svg,
      viewport,
      width,
      height,
      transform: { k: 1, x: 0, y: 0 },
      center: { x: width / 2, y: height / 2, aspect: Math.max(1, width / height) },
    };
    attachCanvasPointer();
    sim = createSimulation(graph.nodes, graph.links, {
      center: scene.center,
      onTick: draw,
      onEnd: () => {
        if (!scene.userZoomed) fit(true);
      },
    });
    // the first frame is already settled: the layout computes before anyone sees it
    sim.settle(reduceMotion() ? 300 : 160);
    fit(false);
    if (!reduceMotion()) sim.restart(0.3);

    resizeObserver = new ResizeObserver(() => {
      // a hidden canvas measures 0 × 0: keep the last real size for the physics
      if (!scene || !canvas.clientWidth || !canvas.clientHeight) return;
      scene.width = canvas.clientWidth;
      scene.height = canvas.clientHeight;
      scene.center.x = scene.width / 2;
      scene.center.y = scene.height / 2;
      scene.center.aspect = Math.max(1, scene.width / scene.height);
      if (!scene.userZoomed) fit(false);
    });
    resizeObserver.observe(canvas);
  }

  // The kind mix of a node as a ring of one segment per kind, with a small
  // gap between segments.
  function ringGroup(node) {
    const group = svgElement("g", { class: "gh-net-ring" });
    const thickness = node.type === "repo" ? 4 : 3;
    const inner = node.r + 1.5;
    const outer = inner + thickness;
    const parts = KINDS.filter((kind) => node.kinds.get(kind));
    if (!parts.length) return group;
    if (parts.length === 1) {
      group.append(
        svgElement("circle", {
          class: `kind-${parts[0]}`,
          r: (inner + outer) / 2,
          "stroke-width": thickness,
        })
      );
      return group;
    }
    const pad = 0.09;
    let angle = 0;
    for (const kind of parts) {
      const span = (node.kinds.get(kind) / node.total) * Math.PI * 2;
      if (span > pad * 1.5) {
        group.append(
          svgElement("path", {
            class: `kind-${kind}`,
            d: arcPath(inner, outer, angle + pad / 2, angle + span - pad / 2),
          })
        );
      }
      angle += span;
    }
    return group;
  }

  function draw() {
    for (const link of graph.links) {
      for (const line of [link.hit, link.line]) {
        line.setAttribute("x1", link.source.x.toFixed(1));
        line.setAttribute("y1", link.source.y.toFixed(1));
        line.setAttribute("x2", link.target.x.toFixed(1));
        line.setAttribute("y2", link.target.y.toFixed(1));
      }
    }
    for (const node of graph.nodes) {
      const at = `translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})`;
      node.element.setAttribute("transform", at);
      node.label.setAttribute("transform", at);
    }
  }

  // ----- view transform ----------------------------------------------------

  function applyTransform() {
    const { k, x, y } = scene.transform;
    scene.viewport.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
  }

  function fit(animate) {
    if (!scene || !graph.nodes.length) return;
    const pad = 28;
    let x0 = Number.POSITIVE_INFINITY;
    let x1 = Number.NEGATIVE_INFINITY;
    let y0 = Number.POSITIVE_INFINITY;
    let y1 = Number.NEGATIVE_INFINITY;
    for (const node of graph.nodes) {
      x0 = Math.min(x0, node.x - node.r - pad);
      x1 = Math.max(x1, node.x + node.r + pad);
      y0 = Math.min(y0, node.y - node.r - pad);
      y1 = Math.max(y1, node.y + node.r + pad + 8);
    }
    const k = clamp(
      0.95 * Math.min(scene.width / (x1 - x0), scene.height / (y1 - y0)),
      SCALE_MIN,
      1.6
    );
    const target = {
      k,
      x: scene.width / 2 - (k * (x0 + x1)) / 2,
      y: scene.height / 2 - (k * (y0 + y1)) / 2,
    };
    if (!animate || reduceMotion()) {
      scene.transform = target;
      applyTransform();
      return;
    }
    const from = { ...scene.transform };
    const started = performance.now();
    const current = scene;
    const step = (now) => {
      if (scene !== current) return;
      const t = Math.min(1, (now - started) / 450);
      const ease = 1 - (1 - t) ** 3;
      scene.transform = {
        k: from.k + (target.k - from.k) * ease,
        x: from.x + (target.x - from.x) * ease,
        y: from.y + (target.y - from.y) * ease,
      };
      applyTransform();
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function zoomBy(factor, px = scene.width / 2, py = scene.height / 2) {
    const { k, x, y } = scene.transform;
    const next = clamp(k * factor, SCALE_MIN, SCALE_MAX);
    const ratio = next / k;
    scene.transform = { k: next, x: px - (px - x) * ratio, y: py - (py - y) * ratio };
    scene.userZoomed = true;
    applyTransform();
  }

  const toGraph = (clientX, clientY) => {
    const rect = scene.svg.getBoundingClientRect();
    const { k, x, y } = scene.transform;
    return { x: (clientX - rect.left - x) / k, y: (clientY - rect.top - y) / k };
  };

  function attachCanvasPointer() {
    const { svg } = scene;
    svg.addEventListener(
      "wheel",
      (event) => {
        if (!(event.ctrlKey || event.metaKey)) return; // plain scrolling keeps scrolling the page
        event.preventDefault();
        const rect = svg.getBoundingClientRect();
        zoomBy(
          Math.exp(-event.deltaY * 0.002),
          event.clientX - rect.left,
          event.clientY - rect.top
        );
      },
      { passive: false }
    );
    let pan = null;
    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest(".gh-net-node")) return;
      pan = { x: event.clientX, y: event.clientY, from: { ...scene.transform }, moved: false };
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointermove", (event) => {
      if (!pan) return;
      const dx = event.clientX - pan.x;
      const dy = event.clientY - pan.y;
      if (!pan.moved && Math.hypot(dx, dy) < CLICK_DISTANCE) return;
      pan.moved = true;
      svg.classList.add("is-panning");
      hideTooltip?.();
      scene.transform = { k: pan.from.k, x: pan.from.x + dx, y: pan.from.y + dy };
      scene.userZoomed = true;
      applyTransform();
    });
    const endPan = (event) => {
      if (!pan) return;
      if (!pan.moved && !event.target.closest(".gh-net-link")) {
        state.selected = null;
        renderPanel();
        applyFocus();
      }
      pan = null;
      svg.classList.remove("is-panning");
    };
    svg.addEventListener("pointerup", endPan);
    svg.addEventListener("pointercancel", endPan);
    svg.addEventListener("keydown", (event) => {
      if (event.key === "Escape") clearSelection();
    });
  }

  function attachNodePointer(node) {
    const element = node.element;
    let drag = null;
    element.addEventListener("pointerenter", () => setHover({ kind: "node", id: node.id }));
    element.addEventListener("pointerleave", () => setHover(null));
    element.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      drag = { x: event.clientX, y: event.clientY, moved: false };
      element.setPointerCapture(event.pointerId);
    });
    element.addEventListener("pointermove", (event) => {
      if (!drag) return;
      if (!drag.moved) {
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < CLICK_DISTANCE) return;
        drag.moved = true;
        hideTooltip?.();
        element.classList.add("is-dragging");
        if (!reduceMotion()) sim.hold(0.25);
      }
      const point = toGraph(event.clientX, event.clientY);
      node.fx = point.x;
      node.fy = point.y;
      if (reduceMotion()) sim.settle(2);
    });
    const endDrag = () => {
      if (!drag) return;
      const { moved } = drag;
      drag = null;
      element.classList.remove("is-dragging");
      if (moved) {
        node.pinned = true;
        node.pin.removeAttribute("display");
        sim.hold(0);
        return;
      }
      state.selected = state.selected === node.id ? null : node.id;
      renderPanel();
      applyFocus();
    };
    element.addEventListener("pointerup", endDrag);
    element.addEventListener("pointercancel", endDrag);
    element.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      node.fx = null;
      node.fy = null;
      node.pinned = false;
      node.pin.setAttribute("display", "none");
      if (reduceMotion()) sim.settle(120);
      else sim.restart(0.3);
    });
  }

  // ----- focus: what is lit --------------------------------------------------

  let hoverTimer = null;
  function setHover(hover) {
    clearTimeout(hoverTimer);
    if (hover) {
      state.hover = hover;
      applyFocus();
      renderPanel();
      return;
    }
    // a short grace keeps the panel from flickering between adjacent nodes
    hoverTimer = setTimeout(() => {
      state.hover = null;
      applyFocus();
      renderPanel();
    }, 140);
  }

  function clearSelection() {
    if (!state.selected && !state.hover) return;
    state.selected = null;
    state.hover = null;
    hideTooltip?.();
    renderPanel();
    applyFocus();
  }

  // The lit neighbourhood: a node with its links and neighbours, or one link
  // with its two ends; nothing when nothing is hovered, focused or selected.
  function litSet() {
    const focus =
      state.hover ||
      (state.rowFocus ? { kind: "node", id: state.rowFocus } : null) ||
      (state.selected ? { kind: "node", id: state.selected } : null);
    if (!focus) return null;
    const nodes = new Set();
    const links = new Set();
    if (focus.kind === "link") {
      const link = graph.links.find((candidate) => candidate.id === focus.id);
      if (!link) return null;
      nodes.add(link.login);
      nodes.add(link.repo);
      links.add(link.id);
      return { nodes, links, subject: null };
    }
    nodes.add(focus.id);
    for (const link of graph.links) {
      if (link.login === focus.id) {
        nodes.add(link.repo);
        links.add(link.id);
      } else if (link.repo === focus.id) {
        nodes.add(link.login);
        links.add(link.id);
      }
    }
    return { nodes, links, subject: focus.id };
  }

  function applyFocus() {
    if (!scene || !graph.nodes.length) return;
    const lit = litSet();
    scene.svg.classList.toggle("is-focus", !!lit);
    for (const node of graph.nodes) {
      const on = !!lit && lit.nodes.has(node.id);
      node.element.classList.toggle("is-lit", on);
      node.label.classList.toggle("is-lit", on);
      node.label.classList.toggle("is-subject", !!lit && lit.subject === node.id);
      if (node.id === state.selected) node.selection.removeAttribute("display");
      else node.selection.setAttribute("display", "none");
    }
    for (const link of graph.links) {
      link.element.classList.toggle("is-lit", !!lit && lit.links.has(link.id));
    }
  }

  // ----- tooltips --------------------------------------------------------

  function nodeTooltip(node) {
    if (node.type === "repo") {
      if (!node.total) {
        return `<div class="gh-tooltip-main">${escapeHtml(shortRepo(node.id))}</div><div class="gh-tooltip-sub">no contributions in this period</div>`;
      }
      const top = node.concentrated
        ? ` · ${escapeHtml(node.top.login)} holds ${pct(node.top.repoShare)}`
        : "";
      return (
        `<div class="gh-tooltip-main">${escapeHtml(shortRepo(node.id))}</div>` +
        `<div class="gh-tooltip-sub">${plural(node.total, "contribution")} · ${plural(node.links.length, "person", "people")}${top}</div>`
      );
    }
    const repos = node.links
      .map((link) => `${escapeHtml(shortRepo(link.repo))} ${pct(link.personShare)}`)
      .join(" · ");
    return (
      `<div class="gh-tooltip-main">${escapeHtml(node.id)}${node.bridge ? ' <span class="gh-net-badge">bridge</span>' : ""}</div>` +
      `<div class="gh-tooltip-sub">${plural(node.total, "contribution")} · ${repos}</div>`
    );
  }

  function linkTooltip(link) {
    return (
      `<div class="gh-tooltip-main">${escapeHtml(link.login)} → ${escapeHtml(shortRepo(link.repo))}</div>` +
      `<div class="gh-tooltip-sub">${plural(link.count, "contribution")} · ${pct(link.personShare)} of their work · ${pct(link.repoShare)} of the repo</div>` +
      kindChips(link.kinds)
    );
  }

  // ----- the detail panel ------------------------------------------------

  let panelKey = null;
  function renderPanel() {
    const panel = container.querySelector("[data-panel]");
    if (!panel || !graph) return;
    const subject =
      (state.hover?.kind === "node" ? state.hover.id : null) || state.selected || null;
    const key = `${subject}|${state.selected}|${graph.nodes.length}|${graph.links.length}`;
    if (key === panelKey) return;
    panelKey = key;
    const node = subject ? graph.nodes.find((candidate) => candidate.id === subject) : null;
    panel.innerHTML = !node
      ? overviewHtml()
      : node.type === "person"
        ? personHtml(node)
        : repoHtml(node);
  }

  const weeks = () => `${days / 7} w`;

  function eyebrow(text) {
    const unpin = state.selected
      ? '<button type="button" class="gh-net-unpin" data-unpin title="Unpin (Esc)" aria-label="Unpin">✕</button>'
      : "";
    return `<div class="gh-net-eyebrow">${text}${unpin}</div>`;
  }

  function avatarHtml(login, large) {
    const url = data.avatars?.[login];
    const size = large ? " is-large" : "";
    return url
      ? `<img class="gh-person-avatar gh-net-avatar-img${size}" src="${escapeHtml(avatarUrl(url))}" alt="" loading="lazy" />`
      : `<span class="gh-person-avatar gh-net-avatar-img${size}">${escapeHtml(login.slice(0, 1).toUpperCase())}</span>`;
  }

  function overviewHtml() {
    const people = graph.nodes.filter((node) => node.type === "person");
    const repos = graph.nodes.filter((node) => node.type === "repo" && node.total);
    const bridges = people
      .filter((node) => node.bridge)
      .sort((a, b) => b.links.length - a.links.length || b.total - a.total);
    const concentrated = repos
      .filter((node) => node.concentrated)
      .sort((a, b) => b.top.repoShare - a.top.repoShare);
    const total = people.reduce((sum, node) => sum + node.total, 0);
    const bridgeRows = bridges
      .map(
        (node) => `
          <button type="button" class="gh-net-row" data-node="${escapeHtml(node.id)}">
            ${avatarHtml(node.id)}
            <span class="gh-net-row-text"><span class="gh-net-row-name">${escapeHtml(node.id)}</span>
              <span class="gh-net-row-sub">${node.links.map((link) => `${escapeHtml(shortRepo(link.repo))} ${Math.round(link.personShare * 100)}`).join(" · ")} %</span></span>
            <span class="gh-net-row-num">${plural(node.links.length, "repo")}</span>
          </button>`
      )
      .join("");
    const concentratedRows = concentrated
      .map(
        (node) => `
          <button type="button" class="gh-net-row" data-node="${escapeHtml(node.id)}">
            <span class="gh-net-repo-glyph"></span>
            <span class="gh-net-row-text"><span class="gh-net-row-name">${escapeHtml(shortRepo(node.id))}</span>
              <span class="gh-net-row-sub">${escapeHtml(node.top.login)} · ${plural(node.links.length, "person", "people")}</span></span>
            <span class="gh-net-row-num is-warn">${pct(node.top.repoShare)}</span>
          </button>`
      )
      .join("");
    return `
      ${eyebrow(`Overview · ${weeks()}`)}
      <div class="gh-net-stats">
        <div class="gh-net-stat"><b>${people.length}</b><span>people</span></div>
        <div class="gh-net-stat"><b>${repos.length}</b><span>repos</span></div>
        <div class="gh-net-stat"><b>${total.toLocaleString(LOCALE)}</b><span>contrib.</span></div>
        <div class="gh-net-stat${concentrated.length ? " is-warn" : ""}"><b>${concentrated.length}</b><span>at risk</span></div>
      </div>
      <div class="gh-net-section">
        <h4>Bridges <span>people in 2+ repositories</span></h4>
        ${bridgeRows ? `<div class="gh-net-list">${bridgeRows}</div>` : '<p class="gh-net-none">No one spans two repositories in this period.</p>'}
      </div>
      <div class="gh-net-section">
        <h4>Concentrated <span>one person holds ≥ ${Math.round(CONCENTRATED * 100)} %</span></h4>
        ${concentratedRows ? `<div class="gh-net-list">${concentratedRows}</div>` : '<p class="gh-net-none">Every active repository has shared ownership.</p>'}
      </div>
      <p class="gh-net-help">Hover a node for details, click to pin it here. Drag to rearrange, double-click to release.</p>`;
  }

  function personHtml(node) {
    const rows = node.links
      .map(
        (link) => `
          <button type="button" class="gh-net-row" data-node="${escapeHtml(link.repo)}">
            <span class="gh-net-repo-glyph"></span>
            <span class="gh-net-row-text"><span class="gh-net-row-name">${escapeHtml(shortRepo(link.repo))}</span>
              <span class="gh-net-bar"><i style="width:${link.personShare * 100}%"></i></span></span>
            <span class="gh-net-row-num">${link.count} · ${pct(link.personShare)}</span>
          </button>`
      )
      .join("");
    return `
      ${eyebrow(`${state.selected === node.id ? "Pinned" : "Person"} · ${weeks()}`)}
      <div class="gh-net-head">
        ${avatarHtml(node.id, true)}
        <div class="gh-net-who">
          <div class="gh-net-login">${escapeHtml(node.id)}${node.bridge ? '<span class="gh-net-badge">bridge</span>' : ""}</div>
          <div class="gh-net-meta">${plural(node.total, "contribution")} · ${plural(node.links.length, "repository", "repositories")}</div>
        </div>
      </div>
      ${mixHtml(node.kinds, node.total)}
      <div class="gh-net-section">
        <h4>Repositories <span>share of their work</span></h4>
        <div class="gh-net-list">${rows}</div>
      </div>
      <div class="gh-net-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-drill="timeline" title="The timeline, refined by this author">→ timeline</button>
        <button type="button" class="btn btn-ghost btn-sm" data-drill="contributors" title="This person's row in the contributors grid">▦ contributors</button>
        <a class="btn btn-ghost btn-sm" href="${escapeHtml(`${GITHUB_WEB}${encodeURIComponent(node.id)}`)}" target="_blank" rel="noopener">↗ GitHub</a>
      </div>`;
  }

  function repoHtml(node) {
    const head = `
      ${eyebrow(`${state.selected === node.id ? "Pinned" : "Repository"} · ${weeks()}`)}
      <div class="gh-net-head">
        <span class="gh-net-repo-glyph is-large"></span>
        <div class="gh-net-who">
          <div class="gh-net-login">${escapeHtml(shortRepo(node.id))}${node.concentrated ? '<span class="gh-net-badge is-warn">concentrated</span>' : ""}</div>
          <div class="gh-net-meta">${node.total ? `${plural(node.total, "contribution")} · ${plural(node.links.length, "person", "people")}` : "no contributions in this period"}</div>
        </div>
      </div>`;
    const github = `<a class="btn btn-ghost btn-sm" href="${escapeHtml(`${GITHUB_WEB}${node.id.split("/").map(encodeURIComponent).join("/")}`)}" target="_blank" rel="noopener">↗ GitHub</a>`;
    if (!node.total) {
      return `${head}
        <p class="gh-net-help">Nothing landed here in the last ${weeks()}. Widen the period or clear the kind filter.</p>
        <div class="gh-net-actions">${github}</div>`;
    }
    const rows = node.links
      .map(
        (link) => `
          <button type="button" class="gh-net-row" data-node="${escapeHtml(link.login)}">
            ${avatarHtml(link.login)}
            <span class="gh-net-row-text"><span class="gh-net-row-name">${escapeHtml(link.login)}</span>
              <span class="gh-net-bar${node.concentrated && link === node.top ? " is-warn" : ""}"><i style="width:${link.repoShare * 100}%"></i></span></span>
            <span class="gh-net-row-num">${link.count} · ${pct(link.repoShare)}</span>
          </button>`
      )
      .join("");
    const note = node.concentrated
      ? `<p class="gh-net-note"><b>${escapeHtml(node.top.login)}</b> made ${pct(node.top.repoShare)} of the work here. If they are away, this repository stalls.</p>`
      : "";
    return `${head}
      ${mixHtml(node.kinds, node.total)}
      ${note}
      <div class="gh-net-section">
        <h4>People <span>share of the repository</span></h4>
        <div class="gh-net-list">${rows}</div>
      </div>
      <div class="gh-net-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-drill="timeline" title="The timeline, refined by this repository">→ timeline</button>
        ${github}
      </div>`;
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
    const length = target.closest("[data-length]");
    if (length && length.dataset.length !== lengthBy) {
      lengthBy = length.dataset.length;
      storageSet(LENGTH_KEY, lengthBy);
      container.querySelectorAll("[data-length]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.length === lengthBy));
      });
      return renderGraph();
    }
    const zoom = target.closest("[data-zoom]");
    if (zoom && scene) {
      if (zoom.dataset.zoom === "fit") {
        scene.userZoomed = false;
        fit(true);
      } else zoomBy(zoom.dataset.zoom === "in" ? 1.35 : 1 / 1.35);
      return;
    }
    const row = target.closest("[data-node]");
    if (row) {
      state.selected = row.dataset.node;
      state.rowFocus = null;
      renderPanel();
      applyFocus();
      return;
    }
    if (target.closest("[data-unpin]")) return clearSelection();
    const drill = target.closest("[data-drill]");
    if (drill) {
      const node = graph.nodes.find((candidate) => candidate.id === subjectId());
      if (!node) return;
      if (drill.dataset.drill === "contributors") onContributors(node.id);
      else
        onTimeline([
          node.type === "person"
            ? { field: "actor", value: node.id }
            : { field: "repo", value: node.id },
        ]);
    }
  });

  const subjectId = () =>
    (state.hover?.kind === "node" ? state.hover.id : null) || state.selected || null;

  // a panel row or a repo chip under the pointer lights its node
  const lightRow = (event) => {
    const row = event.target.closest("[data-node], [data-node-hover]");
    const id = row?.dataset.node ?? row?.dataset.nodeHover;
    if (event.type === "pointerover" && id) {
      if (state.rowFocus === id) return;
      state.rowFocus = id;
      applyFocus();
    } else if (event.type === "pointerout" && state.rowFocus) {
      const to = event.relatedTarget?.closest?.("[data-node], [data-node-hover]");
      if (to === row) return;
      state.rowFocus = null;
      applyFocus();
    }
  };
  container.addEventListener("pointerover", lightRow);
  container.addEventListener("pointerout", lightRow);

  container.addEventListener("input", (event) => {
    const slider = event.target.closest("[data-share]");
    if (!slider) return;
    minShare = Number(slider.value);
    storageSet(SHARE_KEY, String(minShare));
    container.querySelector("[data-share-value]").textContent = `${minShare} %`;
    renderGraph();
  });

  container.addEventListener("keydown", (event) => {
    if (event.key === "Escape") clearSelection();
  });

  function toggle(set, value) {
    if (set.has(value)) set.delete(value);
    else set.add(value);
    render();
  }

  return {
    /** Show a feed's network (state and positions reset when the feed changes). */
    open(nextFeed) {
      const changed = feed?.id !== nextFeed.id;
      feed = nextFeed;
      if (changed) {
        state = freshState();
        placed = new Map();
        data = null;
        error = null;
      }
      if (changed || (!data && !loading)) load();
      else render();
    },
    refresh: () => load({ quiet: true }),
    close() {
      feed = null;
      forget();
      teardownScene();
    },
    get days() {
      return days;
    },
    /** Change the period; `reload` false (the view is hidden) just forgets the data. */
    setDays(next, { reload = true } = {}) {
      if (!RANGES.includes(next) || next === days) return;
      days = next;
      storageSet(RANGE_KEY, String(next));
      state.selected = null;
      if (reload) load();
      else forget();
    },
    get busy() {
      return loading;
    },
    meta() {
      if (!data) return null;
      return {
        total: data.counts.reduce((sum, entry) => sum + entry.count, 0),
        fetched_at: data.fetched_at,
        rate_remaining: data.rate_remaining,
        rate_limit: data.rate_limit,
      };
    },
  };
}
