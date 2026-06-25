# ═══════════════════════════════════════════════════════════════════════════════
# Kerosene Vault — Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# The Vault is the Hardware Attestation + Key Provisioning node.
# It MUST NOT expose any TCP ports to the host. It is reachable ONLY via
# its Tor v3 Hidden Service, enforced by having no 'ports:' in docker-compose.
#
# Security posture:
#   - Distroless nonroot runtime (no shell, no writable OS layer)
#   - IPC_LOCK via cap_add for mlock() of key material in RAM
#   - Maven build matches Maven pom.xml in /vault
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM maven:3.9.6-eclipse-temurin-21-jammy AS builder

WORKDIR /build

# ── Layer 1: Dependency resolution ────────────────────────────────────────────
COPY pom.xml .
RUN mvn dependency:go-offline --no-transfer-progress -q

# ── Layer 2: Source compilation ───────────────────────────────────────────────
COPY src src/
RUN mvn clean package --no-transfer-progress -DskipTests

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM gcr.io/distroless/java21-debian12:nonroot AS runtime

WORKDIR /vault

COPY --chown=65532:65532 --from=builder /build/target/kerosene-vault-*.jar vault.jar

# Run as nonroot. IPC_LOCK is granted at the compose level via cap_add.
# NEVER run as root in production — TPM attestation policy enforces nonroot UID.
USER 65532:65532

ENTRYPOINT [ \
  "java", \
  "-XX:+UseContainerSupport", \
  "-XX:MaxRAMPercentage=75.0", \
  "-Djava.security.egd=file:/dev/./urandom", \
  "-jar", "vault.jar" \
]
