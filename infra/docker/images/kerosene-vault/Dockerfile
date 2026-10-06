# syntax=docker/dockerfile:1.7

# Base manifests are digest-pinned so an independently repeated build cannot
# silently select a different compiler or runtime filesystem.
FROM rust:1.90.0-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f AS build
WORKDIR /src
ARG CARGO_FEATURES=
ARG SOURCE_DATE_EPOCH=0
ENV CARGO_BUILD_JOBS=1
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY apps ./apps
COPY crates ./crates
# Tests run in CI before image construction. The image stage performs one
# reproducible release build to keep peak memory bounded.
RUN if [ -n "$CARGO_FEATURES" ]; then \
      cargo build --locked --release -p kerosene-vault-app --no-default-features --features "$CARGO_FEATURES" --bin kerosene-vault; \
    else \
      cargo build --locked --release -p kerosene-vault-app --bin kerosene-vault; \
    fi

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
ARG SOURCE_COMMIT=unbound
ARG SOURCE_TREE_DIGEST=unbound
ARG BUILD_RECIPE_DIGEST=unbound
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /src/target/release/kerosene-vault /usr/local/bin/kerosene-vault
LABEL org.opencontainers.image.source="https://github.com/Daniel-Astrofer/vault" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.kerosene.source-tree-digest="${SOURCE_TREE_DIGEST}" \
      io.kerosene.build-recipe-digest="${BUILD_RECIPE_DIGEST}"
ENV ATTESTATION_MODE=sim
ENV VAULT_LISTEN_ADDR=0.0.0.0:7701
EXPOSE 7701
ENTRYPOINT ["/usr/local/bin/kerosene-vault"]
