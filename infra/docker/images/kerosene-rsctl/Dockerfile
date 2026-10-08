FROM rust:1.89-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --locked --release -p kerosene-rsctl

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 kerosene \
    && useradd --create-home --uid 65532 --gid 65532 kerosene
COPY --from=build /src/target/release/kerosene-rsctl /usr/local/bin/kerosene-rsctl
USER 65532:65532
ENTRYPOINT ["kerosene-rsctl"]
