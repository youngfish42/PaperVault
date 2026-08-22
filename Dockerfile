# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend

WORKDIR /build/web-vue
COPY web-vue/package.json web-vue/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web-vue/ ./
RUN npm run build


FROM python:3.10-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/papervault \
    HOST=0.0.0.0 \
    PORT=5001

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin papervault

COPY --chown=papervault:papervault app.py data_artifacts.py gunicorn.conf.py ./
COPY --chown=papervault:papervault papervault/ ./papervault/
COPY --chown=papervault:papervault collector/ ./collector/
COPY --from=frontend --chown=papervault:papervault /build/static/dist ./static/dist
COPY --chown=papervault:papervault docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /app/cache && chown papervault:papervault /app/cache

USER papervault

EXPOSE 5001

# The first boot may need to download the HF artifact and build the derived
# SQLite/FTS5 index. Subsequent boots reuse it in milliseconds when /app/cache
# is mounted persistently.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5m --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '5001') + '/api/v1/healthz', timeout=5)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
