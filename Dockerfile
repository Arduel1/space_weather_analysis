FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install the locked runtime dependencies into /app/.venv; this layer is rebuilt only when the lockfile or project metadata change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --no-install-project

COPY src ./src
COPY data ./data

ENV PATH="/app/.venv/bin:$PATH"

# Every Compose service sets its own command; the default starts the Streamlit dashboard.
EXPOSE 8501
CMD ["streamlit", "run", "src/streamlit_app/main.py", "--server.address=0.0.0.0"]
