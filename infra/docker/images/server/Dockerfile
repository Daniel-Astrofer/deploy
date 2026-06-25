# ═══════════════════════════════════════════════════════════════════════════════
# Kerosene App — Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   Stage 1 (builder): eclipse-temurin:21-jdk-bookworm
#     → Debian Bookworm + glibc. Required for ABI compatibility with the
#       distroless runtime. Alpine (musl) binaries crash silently when JNI
#       calls (mlock, TPM, SGX) are made against the glibc runtime image.
#
#   Stage 2 (runtime): gcr.io/distroless/java21-debian12:nonroot
#     → No shell, no package manager, no writable layers.
#     → Eliminates the entire attack surface of an OS userland.
#     → UID 65532 (nonroot) — IPC_LOCK is granted via docker-compose cap_add.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM eclipse-temurin:21-jdk AS builder

WORKDIR /workspace

# ── Layer 1: Gradle wrapper (cached independently) ────────────────────────────
COPY gradlew gradlew.bat ./
COPY gradle gradle/
RUN chmod +x gradlew

# ── Layer 2: Dependency resolution (cache until build.gradle.kts changes) ─────
COPY build.gradle.kts settings.gradle.kts ./
COPY gradle.properties ./
COPY kerosene-contracts kerosene-contracts/
COPY kerosene-shared kerosene-shared/
COPY kfe-service kfe-service/
ARG GRADLE_MAX_WORKERS=2
RUN ./gradlew dependencies --no-daemon --max-workers=${GRADLE_MAX_WORKERS} --quiet || true

# ── Layer 3: Source compilation ───────────────────────────────────────────────
COPY src src/
COPY deploy/docker docker/
COPY web-admin-build web-admin-build/
RUN ./gradlew bootJar --no-daemon --max-workers=${GRADLE_MAX_WORKERS} -x test
RUN javac docker/Healthcheck.java -d /workspace/healthcheck

# ── Stage 2: Runtime (Distroless — minimal attack surface) ────────────────────
FROM gcr.io/distroless/java21-debian12:nonroot AS runtime

WORKDIR /app

# Copy the fat JAR. --chown ensures the nonroot user can read it.
COPY --chown=65532:65532 --from=builder /workspace/build/libs/*.jar app.jar
COPY --chown=65532:65532 --from=builder /workspace/healthcheck /app/healthcheck

# UID 65532 matches distroless:nonroot — lowest possible privilege.
# IPC_LOCK (mlock) is granted via docker-compose cap_add, NOT via setuid.
USER 65532:65532

# Port is INTERNAL only — Tor handles all ingress via the hidden service.
# Never bind this port to the host interface.
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=4s --start-period=180s --retries=12 \
  CMD ["java", "-cp", "/app/healthcheck", "Healthcheck", "http://127.0.0.1:8080/health/ready"]

ENTRYPOINT [ \
  "java", \
  "-XX:+UseContainerSupport", \
  "-XX:MaxRAMPercentage=75.0", \
  "-Djava.security.egd=file:/dev/./urandom", \
  "-Djava.awt.headless=true", \
  "-jar", "app.jar" \
]
