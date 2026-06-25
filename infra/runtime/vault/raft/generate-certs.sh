#!/bin/sh
set -eu

CERTS_DIR="${CERTS_DIR:-/vault/certs}"
mkdir -p "$CERTS_DIR"

if ! command -v openssl >/dev/null 2>&1; then
  apk add --no-cache openssl
fi

rm -f "$CERTS_DIR"/*.pem "$CERTS_DIR"/*.srl "$CERTS_DIR"/*.ext

openssl req -x509 -new -nodes -days 3650 \
  -subj "/CN=Vault Raft Local CA" \
  -keyout "$CERTS_DIR/ca-key.pem" \
  -out "$CERTS_DIR/ca.pem"

for node in vault-raft-1 vault-raft-2 vault-raft-3; do
  cat > "$CERTS_DIR/$node.ext" << EXTEOF
subjectAltName = DNS:$node, DNS:localhost, IP:127.0.0.1
EXTEOF

  openssl genrsa -out "$CERTS_DIR/$node-key.pem" 2048

  openssl req -new -key "$CERTS_DIR/$node-key.pem" \
    -subj "/CN=$node" \
    -out "$CERTS_DIR/$node.csr"

  openssl x509 -req -days 3650 \
    -in "$CERTS_DIR/$node.csr" \
    -CA "$CERTS_DIR/ca.pem" \
    -CAkey "$CERTS_DIR/ca-key.pem" \
    -CAcreateserial \
    -extfile "$CERTS_DIR/$node.ext" \
    -out "$CERTS_DIR/$node.pem"

  rm -f "$CERTS_DIR/$node.csr" "$CERTS_DIR/$node.ext"
done

chmod 600 "$CERTS_DIR/ca-key.pem" "$CERTS_DIR"/*-key.pem
chmod 644 "$CERTS_DIR/ca.pem" "$CERTS_DIR"/*.pem
