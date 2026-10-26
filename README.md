# OpsDeck

A personal devops dashboard with three tabs:

- **Links**: cards for the consoles, dashboards and tools you open every day.
- **Logs**: saved Google Cloud Logging queries, with click-to-refine and trace drill-down.
- **GitHub**: activity feeds for an organization or a set of repositories.

FastAPI backend, vanilla JavaScript frontend with no build step, MySQL storage.

## Run with Docker Compose

```bash
cp .env.example .env    # optional
docker compose up -d --build
```

Open http://127.0.0.1:8765. Compose starts the app and a MySQL database; migrations run on startup.

The GitHub tab needs a token in `GITHUB_TOKEN`: a classic personal access token with the `repo` and `read:org` scopes, or the output of `gh auth token`.

The Logs tab needs Google Cloud credentials with `roles/logging.viewer` on the projects you query. Put an Application Default Credentials file at `secrets/gcp-adc.json`, readable by the container user:

```bash
mkdir -p secrets
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
| `GITHUB_TOKEN` | empty | GitHub token; empty means the local `gh` login |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | `127.0.0.1`, `8765` | Address the server listens on |

Architecture notes for contributors are in [CLAUDE.md](CLAUDE.md).
