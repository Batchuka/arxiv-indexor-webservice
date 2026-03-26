FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-interaction --no-ansi --no-root

# Copy project
COPY . .

# SQLite DB will live in /app/arxiv.db (use a volume to persist)
VOLUME ["/app/arxiv.db"]

# Environment variables (pass at runtime via docker run -e or --env-file)
# Required:
#   ANTHROPIC_API_KEY
# Optional (have defaults):
#   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO
#   WEB_HOST, WEB_PORT

EXPOSE 8000

# Default: start web server. Override with "fetch" to run the indexer.
# Usage:
#   docker run --env-file .env -p 8000:8000 arxiv-indexor serve
#   docker run --env-file .env arxiv-indexor fetch
ENTRYPOINT ["python", "-m", "arxiv_indexor"]
CMD ["serve"]
