# PlaceMatch -- production-ready image.
# Build:  docker build -t placematch .
# Run:    docker run --rm -p 8000:8000 --env-file .env placematch
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the running app needs.
COPY app/ ./app/
COPY config/ ./config/
COPY assets/ ./assets/
COPY scripts/ ./scripts/
COPY main.py ./

# Non-root user.
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Safe default: never spend real money unless explicitly overridden at deploy time.
ENV MOCK_LLM=true \
    SQLITE_PATH=/app/data/placematch.db \
    APP_PORT=8000 \
    AGENT_EXECUTION_TIMEOUT_SECONDS=285 \
    RECOMMENDATION_RESERVE_SECONDS=60 \
    TOOL_EXECUTION_TIMEOUT_SECONDS=50 \
    MAX_CONCURRENT_TOOL_REQUESTS=10

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]
