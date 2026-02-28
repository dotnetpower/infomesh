# ── Build stage ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first (layer caching)
COPY pyproject.toml uv.lock ./

# Install production dependencies only
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY infomesh/ infomesh/
COPY seeds/ seeds/
COPY bootstrap/ bootstrap/

# Install the project itself
RUN uv sync --frozen --no-dev


# ── Runtime stage ────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Create non-root user
RUN groupadd --gid 1000 infomesh && \
    useradd --uid 1000 --gid infomesh --create-home infomesh

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code and data
COPY --from=builder /app/infomesh /app/infomesh
COPY --from=builder /app/seeds /app/seeds
COPY --from=builder /app/bootstrap /app/bootstrap

# Set environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    INFOMESH_NODE_DATA_DIR=/data

# Create data directory
RUN mkdir -p /data && chown infomesh:infomesh /data

# Expose ports
#   4001 — P2P listener (libp2p)
#   8080 — Admin API (FastAPI)
EXPOSE 4001 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from pathlib import Path; assert Path('/data/infomesh.pid').exists()" || exit 1

# Run as non-root
USER infomesh

# Data volume
VOLUME ["/data"]

# Default command: start node without dashboard (headless)
ENTRYPOINT ["python", "-m", "infomesh"]
CMD ["start", "--no-dashboard"]
