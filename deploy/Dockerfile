# Trusted development/test toolchain; production browser workers use a separate image.
FROM node:22-bookworm-slim AS node
FROM docker:28-cli AS dockercli
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
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY scripts/dev_entrypoint.py /usr/local/bin/dev_entrypoint.py

USER webmonitor
WORKDIR /workspace
ENTRYPOINT ["python", "/usr/local/bin/dev_entrypoint.py"]
# Interactive development runtime, not an application server.
CMD ["python"]
