# Docker uses Python directly; Conda is only for the host environment.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 webmonitor \
    && useradd --uid 10001 --gid 10001 --create-home webmonitor \
    && chmod -R a+rX /opt/playwright

USER webmonitor
WORKDIR /workspace
# Interactive development runtime, not an application server.
CMD ["python"]
