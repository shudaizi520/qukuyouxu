FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    DATA_ROOT=/data \
    PORT=9511

WORKDIR /app

ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --gid "$APP_GID" qukuyouxu \
    && useradd --uid "$APP_UID" --gid qukuyouxu --home-dir /app --no-create-home --no-log-init qukuyouxu \
    && mkdir -p /data \
    && chown qukuyouxu:qukuyouxu /data

COPY requirements.lock ./
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps . \
    && python -m pip check

USER qukuyouxu
EXPOSE 9511
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "helper.healthcheck"]

CMD ["qukuyouxu"]
