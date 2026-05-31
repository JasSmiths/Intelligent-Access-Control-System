FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 AS base

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

RUN pip install --upgrade pip \
    && pip install uv \
    && uv sync --locked --no-dev --no-editable --compile-bytecode

RUN groupadd --system iacs \
    && useradd --system --gid iacs --home-dir /app --shell /usr/sbin/nologin iacs \
    && mkdir -p /app/data /app/logs \
    && chown -R iacs:iacs /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

FROM base AS development

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --locked --extra dev --no-editable --compile-bytecode \
    && python -m playwright install --with-deps chromium

USER iacs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM development AS test

CMD ["python", "-m", "pytest"]

FROM base AS runtime

USER iacs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
