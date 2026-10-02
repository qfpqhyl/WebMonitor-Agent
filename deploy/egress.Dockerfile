FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends squid unbound ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 egress \
    && useradd --uid 10001 --gid egress --no-create-home --shell /usr/sbin/nologin egress
COPY deploy/egress/squid.conf /etc/squid/squid.conf
COPY deploy/egress/unbound.conf /etc/unbound/webmonitor.conf
COPY deploy/egress/entrypoint.sh /usr/local/bin/egress-entrypoint
RUN chmod 0755 /usr/local/bin/egress-entrypoint
USER 10001:10001
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/egress-entrypoint"]
