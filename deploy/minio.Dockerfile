# syntax=docker/dockerfile:1
# Official asset digests: deploy/minio-artifacts.json. No binary enters Git.
FROM --platform=$BUILDPLATFORM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS binaries
ARG TARGETARCH
# Snapshot the package index too: both the base and downloader are immutable.
RUN printf '%s\n' 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260920T000000Z bookworm main' > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) minio_sha=53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c; \
             mc_sha=ac90da87a35641be5a0ac75d49de5161ddb47d629b5ba01261b0ae9e00aea15f ;; \
      arm64) minio_sha=6c2f3142c94240206123177f4ba1e360daa5d1e0a4962e90757ef4f92c3ab57c; \
             mc_sha=61bb88e7435919834478ddd4d405a6de6d2c227079da5e8ee9655147398819a0 ;; \
      *) echo 'Supported MinIO platforms: linux/amd64, linux/arm64' >&2; exit 1 ;; \
    esac; \
    mkdir /out; \
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      'https://github.com/minio/minio/releases/download/RELEASE.2025-04-22T22-12-26Z/minio.linux-'"$TARGETARCH"'.RELEASE.2025-04-22T22-12-26Z' -o /out/minio; \
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      'https://github.com/minio/mc/releases/download/RELEASE.2025-04-16T18-13-26Z/mc.linux-'"$TARGETARCH"'.RELEASE.2025-04-16T18-13-26Z' -o /out/mc; \
    printf '%s  /out/minio\n%s  /out/mc\n' "$minio_sha" "$mc_sha" | sha256sum --check --strict; \
    chmod 0755 /out/minio /out/mc

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.version="RELEASE.2025-04-22T22-12-26Z" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=binaries /out/minio /out/mc /usr/local/bin/
COPY --from=binaries /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN groupadd --gid 10001 minio \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp minio \
    && mkdir /data \
    && chown 10001:10001 /data
# Mount /data as tmpfs with uid=10001,gid=10001,mode=0700 in Compose.
# A tmpfs hides the directory ownership from this image.
ENV HOME=/tmp MC_CONFIG_DIR=/tmp/.mc MINIO_BROWSER=off
USER 10001:10001
EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
