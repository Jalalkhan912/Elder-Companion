FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

# onnxruntime needs OpenMP
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    HF_HOME=/app/.cache/huggingface \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source edits don't re-download them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data /app/.cache \
    && chown app:app /data /app/.cache

USER app
RUN python -m voice_agent.agent download-files

ENV DB_PATH=/data/companion.db
EXPOSE 8000
CMD ["uvicorn", "voice_agent.web.app:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
