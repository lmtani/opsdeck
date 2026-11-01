# OpsDeck

A personal devops dashboard that runs on your machine:

- **GitHub**: activity feeds for an organization or some of its repositories, with contributors and network views.
- **Logs**: saved Google Cloud Logging queries, with click-to-refine, trace drill-down and filters watched in the background.
- **Links**: cards for the consoles, dashboards and tools you open every day.

What each tab does is described in [docs/features.md](docs/features.md).

## Install

Requirements: [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 if needed).

```bash
uv tool install git+https://github.com/lmtani/opsdeck
opsdeck             # http://127.0.0.1:8765
```

Update with `uv tool upgrade opsdeck`; the database migrates itself on the next start. `opsdeck info` shows the version and where the data is: a SQLite file in the user's data directory (`~/.local/share/opsdeck` on Linux, `~/Library/Application Support/opsdeck` on macOS), created on the first run. The box button in the top bar downloads a backup of everything and restores one, here or on another machine.

The Links tab works right away; the other two need credentials. OpsDeck can also be installed as an app from Chrome or Edge (the install icon in the address bar).

## Credentials

**GitHub**: if the `gh` CLI is logged in, nothing else is needed. Otherwise set `GITHUB_TOKEN` to a [classic token](https://github.com/settings/tokens) with the `repo` and `read:org` scopes. A [fine-grained token](https://github.com/settings/personal-access-tokens) also works for one organization: set its resource owner to the organization and grant *Events* (organization), *Metadata*, *Pull requests* and *Contents* (repository) read access; some organizations must approve it or do not allow it. When the token falls short, the GitHub tab says what to change.

**Logs**: the tab uses Google Cloud Application Default Credentials with `roles/logging.viewer` on the projects you query. Create them once:

```bash
gcloud auth application-default login
```

## Docker Compose

Runs the app with its own MySQL; command cards are off inside the container.

```bash
cp .env.example .env    # set GITHUB_TOKEN here, e.g. echo "GITHUB_TOKEN=$(gh auth token)" >> .env
cp ~/.config/gcloud/application_default_credentials.json secrets/gcp-adc.json
chmod 644 secrets/gcp-adc.json
docker compose up -d --build    # http://127.0.0.1:8765
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPSDECK_DATA_DIR` | the user's data directory | Where the SQLite file and the uploaded card images live |
| `DASHBOARD_DB_URL` | SQLite in the data directory | Database URL, e.g. `mysql+pymysql://user:password@host:3306/opsdeck` (the database is created if missing) |
| `DASHBOARD_DB_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_NAME` | empty, `3306`, `root`, empty, `opsdeck` | MySQL by parts, as Compose sets it |
| `GITHUB_TOKEN` | the `gh` login | Token for the GitHub tab |
| `GOOGLE_APPLICATION_CREDENTIALS` | the gcloud location | Credentials file for the Logs tab |
| `DASHBOARD_COMMANDS` | `on` | `off` disables cards that run local commands |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | `127.0.0.1`, `8765` | Address the server listens on (or `opsdeck --host … --port …`) |

## Contributing

FastAPI backend in ports-and-adapters layers, vanilla JavaScript frontend with no build step; the architecture notes are in [CLAUDE.md](CLAUDE.md).

```bash
uv run opsdeck                      # run from the clone (same data directory as the installed one)
uv run pre-commit install           # once per clone: ruff, mypy, Biome, gitleaks, pip-audit, zizmor
uv run pytest -q
uv run opsdeck revision "add x"     # after editing tables.py: draft the next migration, then review it
```

A clone that kept its data in `data/` (before OpsDeck was a package) has it copied to the data directory on the first start, as long as that directory does not exist yet.
