# syntax=docker/dockerfile:1.7

FROM rust:1.88-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --locked --release --features production -p kerosene-node

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/kerosene-node /usr/local/bin/kerosene-node
USER 1000:1000
EXPOSE 8800
ENTRYPOINT ["/usr/local/bin/kerosene-node"]
