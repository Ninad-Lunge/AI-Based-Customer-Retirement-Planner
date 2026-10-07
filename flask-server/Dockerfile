# Backend image for the AI-Based Customer Retirement Planner (Flask API).
#
# Pinned to CPython 3.12 because TensorFlow 2.17 (a transitive dependency via
# keras) only publishes wheels for CPython 3.9-3.12. Using the slim variant to
# keep the image smaller while still having a glibc base TensorFlow supports.
#
# Build:  docker build -t crp-backend ./flask-server
# Run:    docker run -p 5000:5000 crp-backend

FROM python:3.12-slim AS base

# Faster, quieter, deterministic Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps occasionally needed by scientific wheels. Kept minimal; most
# numpy/pandas/tensorflow wheels are manylinux and need no build toolchain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the application code.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Container-level healthcheck hits the liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/health || exit 1

# Serve with gunicorn (production WSGI server), not the Flask dev server.
# A single worker keeps the TensorFlow/model memory footprint predictable;
# scale with replicas rather than many in-process workers. Generous timeout
# because the first LSTM cold-train can be slow (addressed in Phase 2).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "server:app"]
