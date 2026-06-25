# Build Stage
FROM docker.io/library/golang:1.24-alpine AS builder

WORKDIR /app

# Install protobuf tooling for generated gRPC bindings.
RUN apk add --no-cache git build-base protoc protobuf-dev

# Install protoc generators
RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@latest && \
    go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

COPY go.mod go.sum* ./
RUN go mod download

COPY . .

# Generate gRPC Go files from the protobuf definition
RUN export PATH=$PATH:$(go env GOPATH)/bin && \
    protoc --go_out=. --go_opt=paths=source_relative \
           --go-grpc_out=. --go-grpc_opt=paths=source_relative \
           proto/mpc.proto

RUN go mod tidy

# Build the enclave sidecar.
RUN CGO_ENABLED=0 GOOS=linux go build -o mpc_enclave main.go

# Production Run Stage
FROM docker.io/library/alpine:3.20

# ca-certificates for TLS, netcat for diagnostics, and wget for Docker healthchecks.
RUN apk --no-cache add ca-certificates tzdata netcat-openbsd wget && \
    addgroup -S -g 65532 kerosene && \
    adduser -S -D -H -u 65532 -G kerosene kerosene && \
    mkdir -p /app/encrypted-shards /mnt/mpc-shards && \
    chown -R 65532:65532 /app /mnt/mpc-shards

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app/mpc_enclave .

EXPOSE 50051 8081

# Executa o binario de Go como usuario nao-root.
USER kerosene:kerosene

HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=6 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8081/version"]

ENTRYPOINT ["/app/mpc_enclave"]
