FROM ghcr.io/astral-sh/uv:0.9.27 AS uv

FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --extra studio --no-editable \
    && groupadd --system --gid 10001 tracebisect \
    && useradd --system --uid 10001 --gid tracebisect --home-dir /app tracebisect \
    && mkdir -p /data \
    && chown tracebisect:tracebisect /data

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "tracebisect.studio.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
