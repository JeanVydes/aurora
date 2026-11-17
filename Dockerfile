FROM python:3.11-slim

# uv — gestor de paquetes nativo (Astral)
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y \
    nodejs npm curl git && \
    npm install -g aws-cdk aws-cdk-local && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Virtualenv fuera de /app para que el bind mount (./app -> /app) no lo oculte
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    AWS_ACCESS_KEY_ID=test \
    AWS_SECRET_ACCESS_KEY=test \
    AWS_DEFAULT_REGION=us-east-1 \
    AWS_ENDPOINT_URL=http://emulador:4566 \
    REDIS_HOST=redis \
    REDIS_PORT=6379 \
    PYTHONPATH=/app:/app/lambdas/api

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-progress

CMD ["bash"]
