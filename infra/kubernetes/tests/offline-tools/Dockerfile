ARG GO_IMAGE
ARG REDIS_IMAGE
ARG POSTGRES_IMAGE
FROM ${GO_IMAGE} AS bolt
WORKDIR /build
COPY go.mod go.sum bbolt.go ./
RUN go mod download && CGO_ENABLED=0 go build -trimpath -o /bbolt .
FROM ${REDIS_IMAGE} AS redis
FROM ${POSTGRES_IMAGE}
RUN apk add --no-cache python3 su-exec
COPY --from=redis /usr/local/bin/redis-server /usr/local/bin/redis-cli /usr/local/bin/redis-check-rdb /usr/local/bin/
COPY --from=bolt /bbolt /usr/local/bin/bbolt
COPY fixtures.py admission.py /opt/lab/
ENTRYPOINT ["python3"]
