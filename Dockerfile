# syntax=docker/dockerfile:1.7

# Distroless runtime: no shell, no package manager, non-root user (uid 65532).
# `distroless/python3-debian12` ships Python 3.11, below the project's
# `requires-python = ">=3.12"`, so the base is `distroless/base-debian12` and
# CPython 3.12 comes from the official `python:3.12-slim-bookworm` image,
# which is the same Debian 12. The project needs no native system libraries.

########################################################################
# 1. Python dependencies
########################################################################
FROM python:3.12-slim-bookworm AS dependencies

COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Dependencies get their own layer, invalidated only when the lock changes;
# then the opsdeck package itself (code and web assets) is installed into the
# same environment, not editable, so the runtime needs nothing but /app/.venv.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY opsdeck ./opsdeck
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Empty directory for uploaded card images. Copied with the runtime user as
# owner, so a fresh volume mounted there starts writable.
RUN mkdir -p /seed/data/images

# CPython's own shared libraries live in the multiarch directory of the
# platform being built (x86_64-linux-gnu, aarch64-linux-gnu). Copy it into a
# neutral tree so the runtime stage needs no architecture-specific path.
RUN mkdir -p /seed/usr/lib && cp -a "/usr/lib/$(uname -m)-linux-gnu" /seed/usr/lib/

########################################################################
# 2. Distroless runtime
########################################################################
FROM gcr.io/distroless/base-debian12:nonroot AS runtime

# Full CPython 3.12 (the official image installs everything under /usr/local).
COPY --from=dependencies /usr/local /usr/local

# CPython's own shared libraries (libffi, libsqlite3, libbz2, liblzma...),
# from the multiarch directory of the target platform. The whole directory on
# purpose: picking .so files by hand turns into an ImportError hunt, and
# source and target are the same Debian 12.
COPY --from=dependencies /seed/usr/lib /usr/lib

COPY --from=dependencies /app/.venv /app/.venv
COPY --from=dependencies --chown=65532:65532 /seed/data /app/data

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # uid 65532 has no writable home; tempfile and caches write here.
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    OPSDECK_DATA_DIR=/app/data \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

EXPOSE 8080

# No shell: exec form is required. Single-process uvicorn: scale by replica,
# not by worker.
ENTRYPOINT ["/app/.venv/bin/python", "-m", "opsdeck"]
