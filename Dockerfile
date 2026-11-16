# Third-party files of the web frontend, installed from the npm lockfile and copied into web/vendor
FROM node:22.11.0-alpine AS vendor

WORKDIR /web

RUN --mount=type=cache,target=/root/.npm \
    --mount=type=bind,source=web/package.json,target=package.json \
    --mount=type=bind,source=web/package-lock.json,target=package-lock.json \
    npm ci --ignore-scripts --no-audit --no-fund

COPY web/build-vendor.sh ./
RUN sh build-vendor.sh

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

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
