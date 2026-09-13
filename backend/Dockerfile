FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072

WORKDIR /app

RUN printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl zstd \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Copied source may retain owner-only host permissions; runtime is non-root.
RUN chmod -R a+rX /app/app /app/alembic \
    && chmod a+r /app/pyproject.toml /app/uv.lock /app/README.md /app/alembic.ini

RUN pip install pip==26.2.1 \
    && pip install uv==0.12.13 \
    && uv sync --locked --no-dev --no-editable --compile-bytecode

RUN groupadd --system iacs \
    && useradd --system --gid iacs --home-dir /app --shell /usr/sbin/nologin iacs \
    && mkdir -p /app/data /app/logs \
    && chown -R iacs:iacs /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

FROM base AS development

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --locked --extra dev --no-editable --compile-bytecode \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright

USER iacs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM development AS test

CMD ["python", "-m", "pytest"]

FROM base AS runtime

USER iacs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
