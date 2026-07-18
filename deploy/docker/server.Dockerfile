FROM ghcr.io/astral-sh/uv:0.11.29-python3.12-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY src/server/pyproject.toml src/server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/server/ ./
RUN uv sync --locked --no-dev --no-install-project \
    && groupadd --gid 10001 stock-sync \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin stock-sync

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
