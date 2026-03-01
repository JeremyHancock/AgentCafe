# AgentCafe — production-hardened multi-stage build.
# See docker-compose.yml for how each service uses this image.
#
# Stage 1: Install dependencies (cached unless pyproject.toml changes)
# Stage 2: Copy only runtime files into a clean slim image

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies and Python packages
COPY pyproject.toml .
COPY agentcafe/__init__.py agentcafe/__init__.py
RUN pip install --no-cache-dir --prefix=/install ".[wizard]"

# --- Stage 2: Runtime ---
FROM python:3.12-slim

# Security: run as non-root user
RUN groupadd -r cafe && useradd -r -g cafe -d /app -s /sbin/nologin cafe

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code, templates, and design files
COPY agentcafe/ agentcafe/
COPY docs/design/ docs/design/

# Create data directory for SQLite (writable by cafe user)
RUN mkdir -p /app/data && chown -R cafe:cafe /app /app/data

USER cafe

# Default: run the Cafe (override in docker-compose for backends)
EXPOSE 8000
CMD ["uvicorn", "agentcafe.main:app", "--host", "0.0.0.0", "--port", "8000"]
