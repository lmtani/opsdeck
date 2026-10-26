// Thin wrapper around the backend HTTP API.

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const isJson = (response.headers.get("content-type") || "").includes("json");
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function json(method, payload) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

export const api = {
  health: () => request("/api/health"),

  listLinks: () => request("/api/links"),
  createLink: (data) => request("/api/links", json("POST", data)),
  updateLink: (id, data) => request(`/api/links/${id}`, json("PUT", data)),
  deleteLink: (id) => request(`/api/links/${id}`, { method: "DELETE" }),
  reorderLinks: (ids) => request("/api/links/order", json("PUT", { ids })),
  runLink: (id) => request(`/api/links/${id}/run`, { method: "POST" }),

  listFilters: () => request("/api/filters"),
  createFilter: (data) => request("/api/filters", json("POST", data)),
  updateFilter: (id, data) => request(`/api/filters/${id}`, json("PUT", data)),
  deleteFilter: (id) => request(`/api/filters/${id}`, { method: "DELETE" }),
  reorderFilters: (ids) => request("/api/filters/order", json("PUT", { ids })),

  runFilter: (id) => request(`/api/filters/${id}/query`, { method: "POST" }),
  queryLogs: (data) => request("/api/logs/query", json("POST", data)),

  listGithubFeeds: () => request("/api/github/feeds"),
  createGithubFeed: (data) => request("/api/github/feeds", json("POST", data)),
  updateGithubFeed: (id, data) => request(`/api/github/feeds/${id}`, json("PUT", data)),
  deleteGithubFeed: (id) => request(`/api/github/feeds/${id}`, { method: "DELETE" }),
  reorderGithubFeeds: (ids) => request("/api/github/feeds/order", json("PUT", { ids })),
  // keepalive lets the request outlive the page (marking seen on pagehide)
  markGithubSeen: (id, seenAt, keepalive = false) =>
    request(`/api/github/feeds/${id}/seen`, { ...json("POST", { seen_at: seenAt }), keepalive }),
  githubUnseen: () => request("/api/github/feeds/unseen"),
  githubToken: () => request("/api/github/token"),
  previewGithubFeed: (data) => request("/api/github/feeds/preview", json("POST", data)),
  runFeed: (id) => request(`/api/github/feeds/${id}/query`, { method: "POST" }),
  queryGithub: (data) => request("/api/github/events/query", json("POST", data)),
  githubOrgRepos: (org) => request(`/api/github/orgs/${encodeURIComponent(org)}/repos`),

  getSettings: () => request("/api/settings"),
  saveSettings: (data) => request("/api/settings", json("PUT", data)),

  uploadImage: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/images", { method: "POST", body: form });
  },
};
