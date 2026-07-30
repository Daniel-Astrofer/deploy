FROM eclipse-temurin:21-jdk-jammy AS build
WORKDIR /src
COPY . .
RUN ./gradlew :kerosene-jctl:installDist --no-daemon

FROM eclipse-temurin:21-jre-jammy
RUN useradd --create-home --uid 65532 operator
COPY --from=build /src/kerosene-jctl/build/install/kerosene-jctl /opt/kerosene-jctl
USER 65532:65532
ENTRYPOINT ["/opt/kerosene-jctl/bin/kerosene-jctl"]
