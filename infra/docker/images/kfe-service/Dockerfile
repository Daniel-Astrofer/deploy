# KFE — Kerosene Financial Engine runtime image.
#
# The image builds the dedicated kfe-service Spring Boot artifact instead of the
# Core bootJar, so the container only carries the KFE HTTP runtime and its direct
# dependencies.

FROM eclipse-temurin:21-jdk AS builder

WORKDIR /workspace

COPY gradlew gradlew.bat ./
COPY gradle gradle/
RUN chmod +x gradlew

COPY settings.gradle.kts build.gradle.kts gradle.properties ./
COPY src src/
COPY --from=contracts . /kerosene-contracts
COPY --from=shared . /kerosene-shared

COPY --from=deploy infra/docker/healthcheck/Healthcheck.java docker/Healthcheck.java
ARG GRADLE_MAX_WORKERS=2
RUN ./gradlew bootJar --no-daemon --max-workers=${GRADLE_MAX_WORKERS} -x test
RUN javac docker/Healthcheck.java -d /workspace/healthcheck

FROM gcr.io/distroless/java21-debian12:nonroot AS runtime

WORKDIR /app

COPY --chown=65532:65532 --from=builder /workspace/build/libs/*-boot.jar app.jar
COPY --chown=65532:65532 --from=builder /workspace/healthcheck /app/healthcheck

USER 65532:65532
EXPOSE 8080

ENV SPRING_PROFILES_ACTIVE=kfe,docker
ENV SPRING_CONFIG_IMPORT=optional:classpath:kfe-service-defaults.properties
ENV KFE_STANDALONE=true
ENV KEROSENE_RUNTIME_ROLE=kfe-service

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
