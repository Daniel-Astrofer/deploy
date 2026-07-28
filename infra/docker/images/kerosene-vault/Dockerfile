# syntax=docker/dockerfile:1.7

# Keep toolchain compatible with dependency requirements (some crates require rustc >= 1.86/1.88).
FROM rust:1.88-bookworm AS build
WORKDIR /src
ARG CARGO_FEATURES=
ENV CARGO_BUILD_JOBS=1
COPY Cargo.toml Cargo.lock ./
COPY src ./src
# Tests run in CI before image construction. The image stage performs one
# reproducible release build to keep peak memory bounded.
RUN if [ -n "$CARGO_FEATURES" ]; then \
      cargo build --locked --release --no-default-features --features "$CARGO_FEATURES" --bin kerosene-vault; \
    else \
      cargo build --locked --release --bin kerosene-vault; \
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
