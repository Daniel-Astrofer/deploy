# noinspection ALL
ui = true
disable_mlock = false

storage "raft" {
  path    = "/vault/data"
  node_id = "vault-raft-3"
}

listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/vault/certs/vault-raft-3.pem"
  tls_key_file  = "/vault/certs/vault-raft-3-key.pem"
}

api_addr     = "https://vault-raft-3:8200"
cluster_addr = "https://vault-raft-3:8201"
