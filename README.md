# OpsDeck

A personal devops dashboard with three tabs:

- **Links**: cards for the consoles, dashboards and tools you open every day.
- **Logs**: saved Google Cloud Logging queries, with click-to-refine and trace drill-down.
- **GitHub**: activity feeds for an organization or a set of repositories.

FastAPI backend, vanilla JavaScript frontend with no build step, MySQL storage.

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

Otherwise create a [classic personal access token](https://github.com/settings/tokens) with the `repo` and `read:org` scopes, which let the dashboard see private repositories and your organizations. Restart with `docker compose up -d` after changing `.env`.

### Logs tab

Copy your Google Cloud Application Default Credentials to `secrets/gcp-adc.json`. They need `roles/logging.viewer` on the projects you query, and the file must be readable by the container user:

```bash
cp ~/.config/gcloud/application_default_credentials.json secrets/gcp-adc.json
chmod 644 secrets/gcp-adc.json
```

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), a MySQL 8 server, and optionally the `gcloud` and `gh` CLIs logged in.

```bash
uv run main.py      # http://127.0.0.1:8765
uv run pytest -q
```

Locally the app uses the `gcloud` CLI for logs and falls back to `gh auth token` for GitHub.

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
