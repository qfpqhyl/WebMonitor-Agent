FROM python:3.12-slim-bookworm AS build
WORKDIR /build
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src ./src
RUN pip wheel --no-deps --wheel-dir /wheel .

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CONTAINER_RUNTIME=true PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 webmonitor \
    && useradd --uid 10001 --gid 10001 --create-home webmonitor \
    && chmod -R a+rX /opt/playwright
RUN mkdir -p /run/webmonitor/db/collector /run/webmonitor/evidence \
    && chown -R 10001:10001 /run/webmonitor \
    && chmod -R 0700 /run/webmonitor
COPY --from=build /wheel /wheel
RUN pip install --no-cache-dir --no-deps /wheel/*.whl && rm -rf /wheel /tmp/requirements.txt
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["webmonitor"]
CMD ["browser-worker"]
