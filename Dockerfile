# =============================================================================
# Multi-stage Dockerfile for Crypto Trading Bot
# Base: Python 3.11-slim for minimal image size
# Runs as non-root user for security
# =============================================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies needed by some Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd --gid 1000 botuser && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash botuser

# Set working directory
WORKDIR /app

# Install Python dependencies first (layer caching optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir streamlit>=1.30.0 plotly>=5.18.0

# Copy application code
COPY bot/ ./bot/
COPY dashboard/ ./dashboard/
COPY main.py config.json config_yolo.json ./

# Create data directory for SQLite database and logs
RUN mkdir -p /app/data /app/logs && \
    chown -R botuser:botuser /app

# Switch to non-root user
USER botuser

# Health check: verify the bot process is running and heartbeat is recent
# The heartbeat file is written by the bot every loop iteration
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import time; from pathlib import Path; \
        hb = Path('/app/data/heartbeat'); \
        assert hb.exists(), 'No heartbeat file'; \
        age = time.time() - float(hb.read_text().strip()); \
        assert age < 300, f'Heartbeat stale: {age:.0f}s old'" \
    || exit 1

# Default command: run the trading bot (CONFIG_FILE env var selects config)
CMD ["python", "main.py"]
