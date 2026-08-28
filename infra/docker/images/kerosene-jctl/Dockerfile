FROM eclipse-temurin:21-jdk-jammy AS build
WORKDIR /src
COPY . .
RUN ./gradlew installDist --no-daemon

FROM eclipse-temurin:21-jre-jammy
RUN groupadd --gid 65532 kerosene \
    && useradd --create-home --uid 65532 --gid 65532 kerosene
COPY --from=build /src/build/install/kerosene-jctl /opt/kerosene-jctl
USER 65532:65532
ENTRYPOINT ["/opt/kerosene-jctl/bin/kerosene-jctl"]
