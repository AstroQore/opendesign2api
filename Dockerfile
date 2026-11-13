FROM python:3.15-slim

LABEL org.opencontainers.image.source="https://github.com/AstroQore/opendesign2api"
LABEL org.opencontainers.image.description="OpenDesign Cloud to OpenAI-compatible gateway"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENDESIGN2API_HOST=0.0.0.0 \
    OPENDESIGN2API_PORT=8792 \
    OPENDESIGN2API_CONFIG=/run/secrets/amr-config.json

RUN groupadd --gid 10001 opendesign2api \
    && useradd --uid 10001 --gid 10001 --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin opendesign2api

WORKDIR /app
COPY --chown=opendesign2api:opendesign2api src/ ./src/
COPY LICENSE NOTICE ./

USER 10001:10001
EXPOSE 8792

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8792/healthz', timeout=4).read()"]

CMD ["python", "-u", "src/server.py"]
