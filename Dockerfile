FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    STACKCHAN_TRANSPORT=relay \
    STACKCHAN_AUDIO_MODE=pcm \
    STACKCHAN_AUDIO_DIR=/tmp/stackchan_audio \
    STACKCHAN_OTEL_LOG=/tmp/stackchan_otel.jsonl \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 stackchan
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY mcp_server/ ./mcp_server/
RUN uv sync --locked --no-dev --no-editable \
    && uv cache clean \
    && chmod -R a+rX /app
USER stackchan
EXPOSE 8080
CMD ["python", "-m", "mcp_server.railway"]
