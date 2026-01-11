FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV POETRY_VIRTUALENVS_CREATE=false
ENV PYTHONPATH=/app/src

WORKDIR /app

# Install Poetry
RUN pip install --upgrade pip && \
    pip install --no-cache-dir poetry

# Copy the pyproject.toml and poetry.lock to the container
COPY pyproject.toml poetry.lock ./

# Install dependencies using Poetry
RUN poetry install --only main --no-interaction --no-ansi --no-root

COPY src ./src
COPY data ./data

# Expose the port on which the app will run
EXPOSE 8080

# CMD ["poetry", "run", "python", "-m", "space_weather_analysis.main"]
CMD ["python", "--version"]
