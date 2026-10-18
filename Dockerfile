# Multi-stage production build for Apartment Expense Manager
# Stage 1: Build the React + TypeScript frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Production Python API & Static Server
FROM python:3.12-slim AS runner
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8787 \
    HOST=0.0.0.0

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application backend and migration scripts
COPY aem/ ./aem/
COPY scripts/ ./scripts/

# Copy built frontend assets from Stage 1
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

EXPOSE 8787

# Run migrations automatically, then start Uvicorn
CMD ["sh", "-c", "python scripts/manage.py init && python scripts/manage.py serve --host 0.0.0.0 --port 8787"]

