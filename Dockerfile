# syntax=docker/dockerfile:1.7

# Runtime distroless: sem shell, sem
# gerenciador de pacotes, usuário não-root (uid 65532). O
# `distroless/python3-debian12` traz Python 3.11 — abaixo do
# `requires-python = ">=3.12"` do projeto — então a base é o
# `distroless/base-debian12` e o CPython 3.12 vem da imagem oficial
# `python:3.12-slim-bookworm`, que é o mesmo Debian 12. Este projeto não
# depende de bibliotecas nativas de sistema.

########################################################################
# 1. Dependências Python
########################################################################
FROM python:3.12-slim-bookworm AS dependencias

COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Camada de dependências separada do código: só invalida quando o lock muda.
# O projeto não é um pacote (sem build-system), então o sync instala apenas
# as dependências e o código é copiado direto para /app no estágio final.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

########################################################################
# 2. Runtime distroless
########################################################################
FROM gcr.io/distroless/base-debian12:nonroot AS runtime

# CPython 3.12 completo (a imagem oficial instala tudo em /usr/local).
COPY --from=dependencias /usr/local /usr/local

# Bibliotecas compartilhadas do próprio CPython (libffi, libsqlite3, libbz2,
# liblzma...). O diretório inteiro de propósito: escolher .so a dedo vira
# caça a ImportError, e origem e destino são o mesmo Debian 12.
COPY --from=dependencias /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu

COPY --from=dependencias /app/.venv /app/.venv

WORKDIR /app

COPY app ./app
COPY web ./web
COPY migrations ./migrations
COPY main.py migrate.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # O uid 65532 não tem home gravável; tempfile e caches escrevem aqui.
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

EXPOSE 8080

# Sem shell: forma exec obrigatória. Uvicorn de processo único: a escala é por
# réplica, não por worker.
ENTRYPOINT ["/app/.venv/bin/python", "main.py"]
