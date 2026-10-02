FROM minio AS minio-tools
FROM python:3.12-slim-bookworm AS build
WORKDIR /build
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src ./src
RUN pip wheel --no-deps --wheel-dir /wheel .

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CONTAINER_RUNTIME=true
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && groupadd --gid 10001 webmonitor \
    && useradd --uid 10001 --gid 10001 --create-home webmonitor
RUN mkdir -p /run/webmonitor/db/api /run/webmonitor/db/agent /run/webmonitor/db/scheduler \
    /run/webmonitor/db/collector /run/webmonitor/db/mailer /run/webmonitor/evidence /run/webmonitor/secrets \
    && chown -R 10001:10001 /run/webmonitor \
    && chmod -R 0700 /run/webmonitor
COPY --from=minio-tools /usr/local/bin/mc /usr/local/bin/mc
COPY scripts/provision_s3.py /opt/webmonitor/provision_s3.py
COPY --from=build /wheel /wheel
RUN pip install --no-cache-dir --no-deps /wheel/*.whl && rm -rf /wheel /tmp/requirements.txt
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["webmonitor"]
CMD ["api"]
