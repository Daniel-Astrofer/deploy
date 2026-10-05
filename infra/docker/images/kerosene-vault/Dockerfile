# syntax=docker/dockerfile:1.7

# Keep toolchain compatible with dependency requirements (some crates require rustc >= 1.86/1.88).
FROM rust:1.88-bookworm AS build
WORKDIR /src
ENV CARGO_BUILD_JOBS=1
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY crates ./crates
# Tests run in CI before image construction. Deploy owns only the production
# image contract; laboratory/dealer feature selection is intentionally absent.
RUN cargo build --locked --release --no-default-features \
      --features production,hybrid --bin kerosene-vault

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && groupadd --gid 65532 kerosene \
    && useradd --no-create-home --uid 65532 --gid 65532 --shell /usr/sbin/nologin kerosene \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=65532:65532 --from=build /src/target/release/kerosene-vault /usr/local/bin/kerosene-vault
USER 65532:65532
EXPOSE 7701
ENTRYPOINT ["/usr/local/bin/kerosene-vault"]
