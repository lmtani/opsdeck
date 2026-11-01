// Side panel with the full context of a GitHub event: the whole text of the
// comment, review, issue, PR or release (rendered by GitHub, sanitized by the
// server), and the code a review comment points at. The timeline stays in
// place; ↑/↓ move to the neighbouring events and Esc closes.
//
// The server decides what an event is about (`event.subject`) and builds the
// GitHub requests; this module only sends that subject back and renders.

import { api } from "./api.js";
import { escapeHtml, LOCALE } from "./ui.js";

const CACHE_MS = 2 * 60_000; // private images in body_html are signed for a few minutes
const HUNK_LINES = 12; // GitHub also shows only the last lines of a commented hunk
const MAX_BURST = 15; // comments of a folded burst read in one go

const REVIEW_LABELS = {
  approved: "approved",
  changes_requested: "changes requested",
  commented: "commented",
  dismissed: "dismissed",
};

const cache = new Map(); // subject key -> { at, promise }

function subjectKey(subject) {
  return `${subject.type}:${subject.repo}:${subject.number}:${subject.item_id}`;
}

function loadContext(subject) {
  const key = subjectKey(subject);
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.promise;
  const promise = api.githubEventContext(subject);
  cache.set(key, { at: Date.now(), promise });
  promise.catch(() => cache.delete(key)); // a failure is retried next time
  return promise;
}

// Subjects to read for an event: each comment of a folded burst, or its own.
export function contextSubjects(event) {
  const comments = event.details?.comments || [];
  const folded = comments.map((comment) => comment.subject).filter(Boolean);
  if (folded.length) return folded.slice(0, MAX_BURST);
  return event.subject ? [event.subject] : [];
}

export const hasContext = (event) => contextSubjects(event).length > 0;

const when = (value) =>
  value
    ? new Date(value).toLocaleString(LOCALE, {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      })
    : "";

// The file and line a review comment is on, with the diff lines it refers to
// when GitHub still has them (replies to outdated comments may come without).
function codeNode(context) {
  const lines = context.diff_hunk ? context.diff_hunk.split("\n") : [];
  const hidden = Math.max(0, lines.length - HUNK_LINES);
  const render = (from) =>
    lines
      .slice(from)
      .map((line) => {
        const kind = line.startsWith("@@")
          ? "is-meta"
          : line.startsWith("+")
            ? "is-add"
            : line.startsWith("-")
              ? "is-del"
              : "";
        return `<span class="ctx-line ${kind}">${escapeHtml(line) || " "}</span>`;
      })
      .join("");
  const where = context.line ? `${context.path}:${context.line}` : context.path;
  const node = document.createElement("div");
  node.className = "ctx-code";
  node.innerHTML = `
    <div class="ctx-file">${escapeHtml(where)}</div>
    ${hidden ? `<button type="button" class="ctx-more">show ${hidden} earlier lines</button>` : ""}
    ${lines.length ? `<pre class="ctx-hunk">${render(hidden)}</pre>` : ""}`;
  node.querySelector(".ctx-more")?.addEventListener("click", (click) => {
    node.querySelector(".ctx-hunk").innerHTML = render(0);
    click.currentTarget.remove();
  });
  return node;
}

function emptyText(subject) {
  if (subject.type === "pull" || subject.type === "issue") return "No description provided.";
  if (subject.type === "review") return "No summary text.";
  return "No text.";
}

const REVIEWER_MARKS = {
  approved: ["✓", "approved"],
  changes_requested: ["±", "requested changes"],
  commented: ["💬", "commented"],
  dismissed: ["–", "review dismissed"],
};

// author, time, review verdict and link of a comment, review or description
function bylineHtml(item, avatarUrl, reviewState = null) {
  const avatar = item.author_avatar
    ? `<img class="ctx-avatar" src="${escapeHtml(avatarUrl(item.author_avatar))}" alt="" />`
    : `<span class="ctx-avatar">${escapeHtml((item.author || "?").slice(0, 1).toUpperCase())}</span>`;
  const review = reviewState
    ? `<span class="ctx-review rv-${escapeHtml(reviewState)}">${escapeHtml(
        REVIEW_LABELS[reviewState] || reviewState
      )}</span>`
    : "";
  const link = item.url
    ? `<a class="ctx-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" title="Open on GitHub">↗</a>`
    : "";
  return `
    <div class="ctx-byline">
      ${avatar}
      <b>${escapeHtml(item.author || "ghost")}</b>
      <time>${escapeHtml(when(item.created_at))}</time>
      ${review}
      ${link}
    </div>`;
}

function bodyNode(html, empty) {
  const body = document.createElement("div");
  if (html) {
    body.className = "gh-markdown";
    body.innerHTML = html; // sanitized by the server (nh3 allowlist)
  } else {
    body.className = "ctx-empty";
    body.textContent = empty;
  }
  return body;
}

// one comment of a conversation: byline and text
function commentNode(comment, avatarUrl, { current = false } = {}) {
  const node = document.createElement("div");
  node.className = `ctx-comment${current ? " is-current" : ""}`;
  node.innerHTML = bylineHtml(comment, avatarUrl);
  node.appendChild(bodyNode(comment.body_html, "No text."));
  return node;
}

// size, branches, labels and reviewers of a pull request
function pullNode(pull) {
  const changed = pull.additions + pull.deletions;
  // GitHub's five-block diffstat: green for additions, red for deletions
  const greens = changed ? Math.round((pull.additions / changed) * 5) : 0;
  const blocks = Array.from(
    { length: 5 },
    (_, index) => `<i class="${changed ? (index < greens ? "is-add" : "is-del") : ""}"></i>`
  ).join("");
  const parts = [
    `<span class="ctx-add">+${pull.additions}</span> <span class="ctx-del">−${pull.deletions}</span> <span class="ctx-diffstat">${blocks}</span>`,
    `${pull.changed_files} ${pull.changed_files === 1 ? "file" : "files"}`,
    `${pull.commits} ${pull.commits === 1 ? "commit" : "commits"}`,
  ];
  if (pull.head_ref) {
    parts.push(
      `<code>${escapeHtml(pull.head_ref)}</code> → <code>${escapeHtml(pull.base_ref)}</code>`
    );
  }
  const labels = pull.labels
    .map(
      (label) =>
        // color is six hex digits or empty (validated server-side)
        `<span class="ctx-label"${label.color ? ` style="--label: #${label.color}"` : ""}>${escapeHtml(label.name)}</span>`
    )
    .join("");
  const reviewers = pull.reviewers
    .map((reviewer) => {
      const [mark, verb] = REVIEWER_MARKS[reviewer.state] || ["○", "review requested"];
      return `<span class="ctx-reviewer rv-${escapeHtml(reviewer.state || "requested")}" title="${escapeHtml(
        `${reviewer.login}: ${verb}`
      )}"><b>${mark}</b>${escapeHtml(reviewer.login)}</span>`;
    })
    .join("");
  const node = document.createElement("div");
  node.className = "ctx-pull";
  node.innerHTML = `
    <div class="ctx-stats">${parts.join('<span class="ctx-sep">·</span>')}</div>
    ${labels ? `<div class="ctx-chips">${labels}</div>` : ""}
    ${reviewers ? `<div class="ctx-chips"><span class="ctx-chips-key">reviewers</span>${reviewers}</div>` : ""}`;
  return node;
}

function itemNode(context, avatarUrl) {
  const node = document.createElement("section");
  node.className = "ctx-item";
  const { subject, thread } = context;

  // a review comment: the code once, then the whole conversation with the
  // event's own comment marked
  if (subject.type === "review_comment" && thread.length) {
    const root = thread[0];
    if (root.path || context.path) node.appendChild(codeNode(root.path ? root : context));
    const conversation = document.createElement("div");
    conversation.className = "ctx-thread";
    for (const comment of thread) {
      conversation.appendChild(
        commentNode(comment, avatarUrl, {
          current: thread.length > 1 && comment.id === subject.item_id,
        })
      );
    }
    node.appendChild(conversation);
    return node;
  }

  node.innerHTML = bylineHtml(context, avatarUrl, context.review_state);
  if (context.pull) node.appendChild(pullNode(context.pull));
  if (context.path) node.appendChild(codeNode(context));
  const lineComments = subject.type === "review" ? thread : [];
  if (context.body_html || !lineComments.length) {
    node.appendChild(bodyNode(context.body_html, emptyText(subject)));
  }

  // a review: each of its line comments with the code it is on
  if (lineComments.length) {
    const heading = document.createElement("div");
    heading.className = "ctx-subhead";
    heading.textContent = `${lineComments.length} line ${lineComments.length === 1 ? "comment" : "comments"}`;
    node.appendChild(heading);
    for (const comment of lineComments) {
      const item = document.createElement("div");
      item.className = "ctx-linecomment";
      if (comment.path) item.appendChild(codeNode(comment));
      item.appendChild(bodyNode(comment.body_html, "No text."));
      node.appendChild(item);
    }
  }
  return node;
}

// `labels`: { kind, state } display names; `avatarUrl`: sizes an avatar URL;
// `onStep(delta)`: open the previous (-1) or next (+1) event; `onClose()`.
export function createContextDrawer({ labels, avatarUrl, onStep, onClose }) {
  const drawer = document.getElementById("gh-drawer");
  const head = document.getElementById("gh-drawer-head");
  const title = document.getElementById("gh-drawer-title");
  const meta = document.getElementById("gh-drawer-meta");
  const body = document.getElementById("gh-drawer-body");
  const openLink = document.getElementById("gh-drawer-open");
  let current = null;
  let seq = 0;

  function renderHead(event) {
    head.innerHTML = `
      <span class="gh-kind kind-${escapeHtml(event.kind)}">${escapeHtml(
        labels.kind[event.kind] || event.kind
      )}</span>
      <span class="ctx-what"><b>${escapeHtml(event.actor)}</b> ${escapeHtml(event.summary)}</span>`;
    title.textContent = event.title || event.summary;
    const parts = [event.repo, when(event.created_at)];
    meta.innerHTML =
      parts.map((part) => `<span>${escapeHtml(part)}</span>`).join("<span>·</span>") +
      (event.state
        ? `<span class="gh-state st-${escapeHtml(event.state)}">${escapeHtml(
            labels.state[event.state] || event.state
          )}</span>`
        : "");
    openLink.href = event.url?.startsWith("https://github.com/")
      ? event.url
      : "https://github.com/";
  }

  async function load(event) {
    const mine = ++seq;
    body.innerHTML =
      '<div class="skeleton ctx-skeleton"></div><div class="skeleton ctx-skeleton is-short"></div>';
    const subjects = contextSubjects(event);
    const results = await Promise.allSettled(subjects.map(loadContext));
    if (mine !== seq) return; // the user moved on to another event
    body.innerHTML = "";
    const failed = results.filter((result) => result.status === "rejected");
    for (const result of results) {
      if (result.status === "fulfilled") {
        // a PR or issue: its current title and state beat the event's snapshot
        if (result.value.title && subjects.length === 1) title.textContent = result.value.title;
        body.appendChild(itemNode(result.value, avatarUrl));
      }
    }
    // a long thread opens on the comment the event is about
    body.querySelector(".ctx-comment.is-current")?.scrollIntoView({ block: "center" });
    if (failed.length) {
      const { reason } = failed[0];
      const gone = reason?.status === 404; // deleted: retrying cannot help
      const note = document.createElement("div");
      note.className = "ctx-error";
      note.innerHTML = `<span>${escapeHtml(reason?.message || "Could not load the full context.")}</span>${
        gone ? "" : ' <button type="button" class="btn btn-ghost btn-sm">Retry</button>'
      }`;
      note.querySelector("button")?.addEventListener("click", () => load(event));
      body.appendChild(note);
    }
  }

  function open(event) {
    current = event;
    renderHead(event);
    drawer.hidden = false;
    requestAnimationFrame(() => drawer.classList.add("is-open"));
    body.scrollTop = 0;
    load(event);
  }

  function close() {
    if (!current) return;
    current = null;
    seq++;
    drawer.classList.remove("is-open");
    drawer.hidden = true;
    onClose();
  }

  document.getElementById("gh-drawer-close").addEventListener("click", close);
  document.addEventListener("keydown", (event) => {
    if (!current || event.altKey || event.ctrlKey || event.metaKey) return;
    if (document.querySelector(".modal-backdrop:not([hidden])")) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    if (event.key === "Escape") {
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      onStep(event.key === "ArrowDown" ? 1 : -1);
    }
  });

  return {
    open,
    close,
    isOpen: () => current !== null,
    currentId: () => current?.id ?? null,
  };
}
