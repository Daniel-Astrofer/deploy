# Vault image and initial readiness boundary

The Vault repository is now a Cargo workspace. The executable belongs to
`apps/kerosene-vault` / package `kerosene-vault-app`; the root package is a
compatibility library and integration-test harness, not a binary target.
The Deploy Dockerfile copies `src`, `apps` and `crates`, then explicitly builds
`-p kerosene-vault-app --bin kerosene-vault --locked`. The default production
feature remains enabled; explicitly selected features are build-time inputs,
not runtime command arguments or release authorization.

Local `cargo metadata --locked --offline --no-deps` verified binary ownership
and the production default, and `cargo check --locked --offline
-p kerosene-vault-app --bin kerosene-vault` passed with existing dependency
warnings. The static Dockerfile regression is not an OCI build or qualification.
The actual image build remains unverified while Docker storage is full.

## Unresolved authenticated readiness

The staging-vault overlay uses an HTTPS HTTP probe for `/v1/health`, without
a client certificate. Vault's mandatory Rustls client verifier rejects clients
without a certificate even for this public HTTP route. The repository's
`mtls_axum_health_requires_client_cert` integration test explicitly covers this
TLS boundary. Kubelet HTTP probes cannot supply the required client identity.

Do not disable client authentication, use forwarded certificate headers or
replace this check with a TCP probe and call it authenticated readiness.
The ordinary staging replicas currently use TCP probes; those prove only that
a listener exists, not health, compatibility, quorum or signer readiness.
A credential-file-based in-image health command, implemented without starting
another Vault runtime/signer, and a qualified exec probe are still required.
Certificate paths must reference already provisioned secrets; no keys or
passphrases may enter command arguments or output. Hostname/CA verification
must remain enabled. Do not apply a future probe to an old image that lacks it.

No live Vault deployment, certificate ceremony, custody mutation or signer
activation was performed. These findings keep full-Cell execution blocked.

## Opt-in manifest preparation

`infra/kubernetes/components/vault-authenticated-readiness` prepares the fixed
exec probe `/usr/local/bin/kerosene-vault --health-probe` for the single `vault`
Deployment. It removes the HTTP/TCP readiness handler, uses a six-second kubelet
timeout (longer than the command's four-second request timeout), and reads
`VAULT_HEALTH_PROBE_URL` from required ConfigMap `vault-health-probe`, key
`local-health-url`. Include that ConfigMap in the approved deployment with a DNS
hostname present in the server certificate and `/v1/health` at the correct port.
The command connects to loopback while verifying that hostname; IP URLs fail.
Existing mounted client cert/key/CA paths are reused, never provisioned here.
The deployment validator rejects this exec probe unless its URL comes from a
nonoptional same-namespace ConfigMap included in the approved deployment, with
the referenced key present and an HTTPS `/v1/health` URL without credentials,
query or fragment. Runtime hostname/certificate qualification is still required.
The exec probe must have an integer timeout greater than four seconds and no
second HTTP/TCP/gRPC handler. Literal IP and numeric host URLs, invalid ports
and boolean/string timeouts are rejected before deployment.

No existing overlay selects this component. Enable it only after qualifying the
exact new image, mounted certificates and Kubernetes exec behavior. Old images
may not implement the command. The render regression proves only the intended
manifest changes; no live exec/readiness qualification or Cell acceptance is
claimed. Recovery restores the previously approved manifest/image explicitly,
without changing mTLS requirements or automatically activating signers.
