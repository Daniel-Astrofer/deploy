#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CERTS_DIR="${CERTS_DIR:-$DEPLOY_DIR/local/certs}"

mkdir -p "$CERTS_DIR"
cd "$CERTS_DIR"

echo "[1/4] Generating Root CA..."
openssl genrsa -out rootCA.key 4096
openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 3650 -out rootCA.crt \
    -subj "/C=CH/ST=Zurich/L=Zurich/O=Kerosene Network/CN=Kerosene Root CA"

echo "[2/4] Generating PostgreSQL Server Certificate..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
    -subj "/C=CH/ST=Zurich/L=Zurich/O=Kerosene Server/CN=kerosene_db"
openssl x509 -req -in server.csr -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
    -out server.crt -days 3650 -sha256

echo "[3/4] Generating Client Certificate..."
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
    -subj "/C=CH/ST=Zurich/L=Zurich/O=Kerosene App/CN=api_system"
openssl x509 -req -in client.csr -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
    -out client.crt -days 3650 -sha256

chmod 0600 server.key rootCA.key client.key

echo "[4/4] Converting client key to PKCS8 DER for Java JDBC..."
openssl pkcs8 -topk8 -inform PEM -outform DER -in client.key -out client.key.der -nocrypt

echo "mTLS certificates generated in $CERTS_DIR"
