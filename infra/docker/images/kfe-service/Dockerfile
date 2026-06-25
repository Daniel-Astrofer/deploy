# KFE — Krinse Financial Engine runtime image
#
# This image intentionally builds the current Kerosene Spring Boot executable while
# selecting the kfe runtime profile. During the staged cutover, the monolith can
# still host adapters while Kubernetes/Docker run KFE as its own workload.

FROM eclipse-temurin:21-jdk AS builder

WORKDIR /workspace

COPY gradlew gradlew.bat ./
COPY gradle gradle/
RUN chmod +x gradlew

COPY settings.gradle.kts build.gradle.kts gradle.properties ./
COPY kerosene-contracts kerosene-contracts/
COPY kerosene-shared kerosene-shared/
COPY kfe-service kfe-service/
RUN ./gradlew :kfe-service:classes --no-daemon --max-workers=${GRADLE_MAX_WORKERS:-2} --quiet || true

COPY src src/
COPY deploy/docker docker/
COPY web-admin-build web-admin-build/
ARG GRADLE_MAX_WORKERS=2
RUN ./gradlew bootJar --no-daemon --max-workers=${GRADLE_MAX_WORKERS} -x test
RUN javac docker/Healthcheck.java -d /workspace/healthcheck

FROM gcr.io/distroless/java21-debian12:nonroot AS runtime

WORKDIR /app

COPY --chown=65532:65532 --from=builder /workspace/build/libs/*.jar app.jar
COPY --chown=65532:65532 --from=builder /workspace/healthcheck /app/healthcheck

USER 65532:65532
EXPOSE 8080

ENV SPRING_PROFILES_ACTIVE=kfe,docker
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
