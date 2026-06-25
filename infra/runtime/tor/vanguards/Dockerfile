FROM debian:bookworm-slim

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends vanguards && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 65532 kerosene && \
    useradd -r -u 65532 -g 65532 -M -s /usr/sbin/nologin kerosene && \
    mkdir -p /var/lib/vanguards /var/run/tor/control && \
    chown -R kerosene:kerosene /var/lib/vanguards /var/run/tor/control

COPY entrypoint.sh /entrypoint.sh
COPY vanguards.conf /etc/vanguards/vanguards.conf

RUN chmod 0555 /entrypoint.sh && \
    chmod 0444 /etc/vanguards/vanguards.conf

USER 65532:65532

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
