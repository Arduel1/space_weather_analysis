# Third-party files of the web frontend, installed from the npm lockfile and copied into web/vendor
FROM node:26.12.1-alpine AS vendor

WORKDIR /web

RUN --mount=type=cache,target=/root/.npm \
    --mount=type=bind,source=web/package.json,target=package.json \
    --mount=type=bind,source=web/package-lock.json,target=package-lock.json \
    npm ci --ignore-scripts --no-audit --no-fund

COPY web/build-vendor.sh ./
RUN sh build-vendor.sh

# Node of the QA image; its binary needs the glibc of a Debian image, which the Alpine image above lacks
FROM node:26.12.1-slim AS node

# Python with uv, the base of the QA image and of the service image
FROM python:3.14-slim AS python

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# QA image with every dependency group in /opt/venv and Node for the syntax check of the frontend modules, built with
# `docker build --target qa`; the project is mounted at /work
FROM python AS qa

RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node

# Caches go to /tmp, as the project is mounted read-only
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    MYPY_CACHE_DIR=/tmp/mypy \
    RUFF_CACHE_DIR=/tmp/ruff \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /work

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --all-groups --no-install-project

# Service image, the last stage and so the one Docker Compose builds
FROM python

ENV PYTHONPATH=/app/src \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1

WORKDIR /app

# The services run as this user; uid 1000 is the first user of most Linux hosts, so files written into the mounted
# data directory belong to that user on the host
RUN useradd --uid 1000 --create-home app

# Install the locked runtime dependencies into /app/.venv; this layer is rebuilt only when the lockfile or project metadata change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --no-install-project

COPY src ./src
# Only the HAPI server list; the other local data files are downloaded at runtime into the mounted data directory.
# The directory belongs to the service user, as services without that mount keep their caches in it.
RUN install -d -o app -g app ./data
COPY data/hapi_config ./data/hapi_config
COPY config ./config
COPY web ./web
COPY --from=vendor /web/vendor ./web/vendor

ENV PATH="/app/.venv/bin:$PATH"

# Code and dependencies stay owned by root, so the services can read but not change them
USER app

# Every Compose service sets its own command; the default starts the Streamlit dashboard.
EXPOSE 8501
CMD ["streamlit", "run", "src/streamlit_app/main.py", "--server.address=0.0.0.0"]
