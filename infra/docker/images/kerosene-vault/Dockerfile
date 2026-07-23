# syntax=docker/dockerfile:1.7

FROM rust:1.85-bookworm AS build
WORKDIR /src
ARG CARGO_FEATURES=
COPY Cargo.toml ./
COPY src ./src
COPY tests ./tests
# Lab default: dealer_lab features from Cargo.toml. Ceremony: --build-arg CARGO_FEATURES=production
RUN if [ -n "$CARGO_FEATURES" ]; then \
      cargo test --no-default-features --features "$CARGO_FEATURES" --offline 2>/dev/null \
        || cargo test --no-default-features --features "$CARGO_FEATURES"; \
      cargo build --release --no-default-features --features "$CARGO_FEATURES" --bin kerosene-vault; \
    else \
      cargo test --offline 2>/dev/null || cargo test; \
      cargo build --release --bin kerosene-vault; \
    fi

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/kerosene-vault /usr/local/bin/kerosene-vault
ENV ATTESTATION_MODE=sim
ENV VAULT_LISTEN_ADDR=0.0.0.0:7701
EXPOSE 7701
ENTRYPOINT ["/usr/local/bin/kerosene-vault"]
