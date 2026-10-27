# OpsDeck

A personal devops dashboard with three tabs:

- **Links**: cards for the consoles, dashboards and tools you open every day.
- **Logs**: saved Google Cloud Logging queries, with click-to-refine and trace drill-down.
- **GitHub**: activity feeds for an organization or a set of repositories.

FastAPI backend, vanilla JavaScript frontend with no build step, MySQL storage.

OpsDeck can be installed as an app (PWA) in Chrome, Edge or other Chromium browsers: open it on `localhost` or over HTTPS and use the install icon in the address bar. The installed app runs in its own window, shows the count of unseen GitHub events on its icon and shows a notice instead of an error page when the server is down. It caches nothing, so the server must be running.

## Run with Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

Open http://127.0.0.1:8765. Compose starts the app and a MySQL database; migrations run on startup. Links work right away; the other two tabs need credentials.

### GitHub tab

Set `GITHUB_TOKEN` in `.env`. If you use the GitHub CLI, its token works:

```bash
echo "GITHUB_TOKEN=$(gh auth token)" >> .env
```

Otherwise create a token in GitHub's Developer settings. Either kind works:

- **Classic** (simplest): [Tokens (classic)](https://github.com/settings/tokens) → Generate new token (classic), with the `repo` and `read:org` scopes. They let the dashboard see private repositories and your organizations.
- **Fine-grained**: [Fine-grained tokens](https://github.com/settings/personal-access-tokens) → Generate new token. Set **Resource owner** to the organization you want to follow, not your user; give it access to all its repositories (or the ones you follow); and grant **Organization permissions → Events: Read-only** and **Repository permissions → Metadata** and **Pull requests: Read-only**. The organization may have to approve the token, and some organizations do not allow fine-grained tokens at all. One token covers one organization.

Restart with `docker compose up -d` after changing `.env`. If the token cannot do what the dashboard needs (rejected, missing scopes, not authorized for SAML single sign-on, or limited to public events), the GitHub tab says so and what to change.

Each day of a feed's timeline opens with a histogram of its activity per half hour and a strip of who contributed, busiest first. Hovering a person shows what they did (kinds, repositories, first and last event) and highlights their share of the histogram; clicking filters the timeline to them.

Clicking an event shows its details; **Full context →** opens a side panel with the whole text of the comment, review, issue, pull request or release, rendered like on GitHub, and the conversation around it: the whole thread of a review comment (with the code it refers to), the line comments of a review, and the size, branches, labels and reviewers of a pull request. With the panel open, clicking another event or pressing ↑/↓ moves to it, and Esc closes it.

To get desktop notifications of new events, hover a feed and click 🔔. The browser asks for permission once and a test notification confirms they reach your desktop (if it does not show up, check the system's notification settings, such as do-not-disturb, for the browser); a feed that notifies shows a bell next to its name. Notifications arrive while OpsDeck is open in some tab, even in the background (it checks every 2 minutes), and clicking one opens the feed. Browsers only allow them on `localhost` or HTTPS, and the choice is kept per browser.

The **● live** switch at the top of the feed list pauses every background update: unread badges, notifications and the 60 s auto refresh. While paused, a feed only updates when you open it or click Refresh. The setting is saved with the theme.

### Logs tab

Copy your Google Cloud Application Default Credentials to `secrets/gcp-adc.json`. They need `roles/logging.viewer` on the projects you query, and the file must be readable by the container user:

```bash
cp ~/.config/gcloud/application_default_credentials.json secrets/gcp-adc.json
chmod 644 secrets/gcp-adc.json
```

No project needs to be configured: each saved filter names its own. If the credentials are missing, unreadable, expired or lack access to a project, the top-bar `logs` dot turns red and the Logs tab says what to fix.

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), a MySQL 8 server, and optionally the `gcloud` and `gh` CLIs logged in.

```bash
uv run main.py      # http://127.0.0.1:8765
uv run pytest -q
```

Locally the app uses the `gcloud` CLI for logs and falls back to `gh auth token` for GitHub.

### Contributing

Install the git hooks once per clone; they check every commit with ruff (lint and format), mypy, [Biome](https://biomejs.dev/) for the frontend and gitleaks for secrets:

```bash
uv run pre-commit install
uv run pre-commit run --all-files   # what CI runs
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_DB_HOST`, `DASHBOARD_DB_PORT` | `127.0.0.1`, `13306` | MySQL server |
| `DASHBOARD_DB_USER`, `DASHBOARD_DB_PASSWORD` | `root`, `1234` | MySQL credentials |
| `DASHBOARD_DB_NAME` | `opsdeck` | Database, created if missing |
| `DASHBOARD_LOG_PROVIDER` | `gcloud` | `api` uses the Cloud Logging client with `GOOGLE_APPLICATION_CREDENTIALS` |
| `DASHBOARD_COMMANDS` | `on` | `off` disables cards that run local commands |
| `GITHUB_TOKEN` | empty | Token for the GitHub tab; when empty, the local `gh` login is used |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | `127.0.0.1`, `8765` | Address the server listens on |

Architecture notes for contributors are in [CLAUDE.md](CLAUDE.md).
