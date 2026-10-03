# Initial admission verifier (not integrated into install)

`infra/governance/admission.go` verifies the Contracts Cell admission v1 payload
against an independently trusted Bank policy and caller-established bindings.
The entire canonical admission is signed by distinct pinned Ed25519 members;
threshold, roster and public keys never come from the envelope. Numeric limits,
one-hour lifetime, exact expiry, cluster/Cell/operator/change/approval bindings,
canonical signature encoding, duplicate JSON keys and bounded input are checked.

The function returns the canonical payload digest, not deployment permission.
Its caller must first verify the release consensus proof and use that result's
approval digest, authenticate the operator, and read the live kube-system UID
through the bound authenticated cluster connection. Passing untrusted expected
values would defeat binding. Local signatures are not an ordered admission.

The additive `verify-admission` CLI is read-only. It accepts the same trusted
anchor, proof, release digest and sequence as `verify`, plus `--admission`,
`--cell-id`, `--cluster-uid`, `--operator-id` and `--change-id`. It first verifies
actual ordered consensus and derives the required approval digest from that
result; there is no caller-selected approval-digest flag. Caller-supplied cluster
and operator bindings must still come from independent authenticated observation.
Output explicitly has `nonceConsumed:false` and `installAuthorized:false`.

There is deliberately no installer integration yet. Authoritative durable
nonce consumption, cross-host concurrency/replay handling, exact-operation
recovery are pending. Keep installation capability
blockers intact. No signer activation, credentials, SQL or Kubernetes writes
occur during verification. Failed verification has no state to roll back; a
future consuming registry must retain failed operation ownership for recovery.

Validation: `go test -race ./...` under `infra/governance` exercises actual
Ed25519 signatures with fresh synthetic keys, minority/duplicate/unknown signers,
tampering, substituted bindings, validity boundaries and strict JSON rejection.
This is not whole-Cell acceptance or distributed replay qualification.

## Shared registry primitive

`admissionOperatorIdentity` derives transport identity from the actual completed
mandatory-mTLS listener state, verified certificate chain, current certificate
validity and independently provisioned SPKI pins. Duplicate pins across operators
are rejected rather than selecting an arbitrary identity. Caller-provided
operator IDs or forwarded certificate headers are not inputs. The actual TLS
fixture exercises this helper after handshake, including refusal of a CA-trusted
but unpinned client before accepted startup. This is **not** administrative
role/session authorization: the future service must enforce existing operator
authorization and bind this identity to the admission's operator before effects.
No HTTP endpoint or caller-selected pin policy is enabled by this helper.

`authorizeAdmissionOperator` adds independently provisioned Bank administrative
grants on top of that transport identity: exact Cell allowlists and explicit
`consume` / `inspect-recovery` operation allowlists. Wildcards, duplicate scopes,
unknown operations and ambiguous identity policies are refused. A valid pinned
certificate alone cannot act on another Cell or consume using a recovery-only
grant. TLS tests enforce these restrictions after an actual handshake. Initial
admission cannot rely on the absent target Cell's Core for authorization; this
policy belongs to an independently provisioned Bank service. No HTTP endpoint
or install integration is enabled yet, and update/resume rights
are not implied by these initial-admission grants.

`loadAdmissionOperatorPolicy` reads a bounded protected regular file with schema
`kerosene.bank-admission-operator-policy/v1` and exactly `schema` / `operators`.
Each operator has exactly `spkiPins`, `cells`, `operations`; structural fields and
all scopes must validate before use. The file's exact-byte SHA-256 digest must be
independently provisioned with the service. Duplicate JSON keys, unknown fields,
empty/ambiguous policy, shared-writable files and digest changes are refused.
Neither a caller-supplied hash nor a hash computed merely to trust a candidate
file is an authority. The TLS fixture loads a pinned policy before authentication.
Policy loader plumbing into a production service remains pending; no auto-reload
or policy-write endpoint exists. Rotation requires externally reviewed provisioning
and explicit service restart, not admission request contents.

`openAdmissionRegistry` prepares a bounded-pool connector with mandatory
`sslmode=verify-full`, explicit CA and client certificate/key, fixed connection
and SQL/lock timeouts and protected password reference. It accepts typed host,
port/database/role fields, not arbitrary DSNs or caller-selected TLS modes.
Inherited `PG*` variables are refused rather than silently overriding connection
or SQL options. Credential reads are bounded and refuse final symlinks, FIFOs,
directories and unsafe private-file permissions; parent directories must remain
operator-controlled. TLS material is validated and passed inline to avoid the
driver's home-directory certificate defaults. The returned pool is lazy: callers
must establish/ping the authenticated connection before treating it as usable.
Real TLS tests exercise the actual connector against a minimal loopback
PostgreSQL-wire peer requiring a trusted client certificate: valid handshake/Ping,
wrong server CA, wrong hostname, untrusted client and server refusal of TLS.
Failures never reach accepted startup. Synthetic certificates live in private
temporary test directories. This qualifies the tested transport paths, not SQL
against a TLS-enabled PostgreSQL server, production PKI or endpoint deployment;
those integrated qualifications remain pending.

The host-only `infra/tests/cell-admission-registry-tls-lab.py` now qualifies the
combined SQL/transport path against a fresh real PostgreSQL 17 server. Run with
`CELL_REGISTRY_LOCAL_TLS_LAB=true python3 infra/tests/cell-admission-registry-tls-lab.py`.
It needs local PostgreSQL 17 binaries and OpenSSL, not Docker/sudo. The server
binds only loopback, rejects non-TLS connections and requires a trusted client
certificate; the service role also authenticates with SCRAM. The actual secure
Go connector verifies `pg_stat_ssl` reports TLS and client identity before
running consumption/concurrency/recovery tests. All five harness tests passed
in the isolated lab. This remains synthetic PKI and in-process consensus proof
qualification, not a production Bank endpoint or full Cell installation.

Every invocation retains its private directory, synthetic keys, database and
logs, and stops only its owned server via the exact data directory. Inspect
retained logs after failure; never repoint cleanup/stop commands at another
cluster. No Docker containers, images, volumes or existing PostgreSQL are changed.

`consumeOrderedCellAdmission` now composes consensus/quorum verification with a
single parameterized registry INSERT. Its approval digest is derived from real
consensus and the database clock rechecks validity. Unique conflicts or expiry
require explicit recovery; connection/cancellation errors report an uncertain
outcome rather than asserting that consumption did not occur. No record is
updated/deleted and no automatic retry runs. This helper is not exposed through
the inspection CLI or installer. Production connection identity/TLS enforcement
and authenticated service integration remain
pending. The isolated loopback test now executes `insertVerifiedAdmission` with
the real restricted PostgreSQL role, checking one-winner concurrency, replay
after reconnect and database-clock expiry. The same opt-in harness also exercises
`consumeOrderedCellAdmission` using actual signed CometBFT light blocks,
approval transaction/application-state proof and quorum-signed admission: invalid
committed state writes nothing, valid proof records one consumption, and replay
requires explicit recovery without altering the record. The light blocks use
synthetic in-process validators, not a live multi-server consensus network.
This does not qualify production TLS, operator authentication or deploy startup.

`inspectConsumedCellAdmission` is a read-only recovery inspection. It requires
current valid consensus trust, exact stored admission fields/digest and quorum
signatures valid at the database-recorded original consumption time. Therefore
the expired initial consumption window does not erase existing operation history.
Changed operator/change/Cell/cluster bindings, altered proof and missing records
are refused. It neither clears consumption nor grants runtime-resume permission;
recovery still requires authenticated operator policy and qualified operation
journal/runtime reconciliation. The PostgreSQL integration test covers exact
inspection, later inspection after initial expiry, altered operator and invalid
consensus refusal, and verifies that the registry record remains unchanged.

`infra/runtime/postgres/cell-admission-registry.sql` creates a fresh private schema
in a separately provisioned Bank registry database. Primary/unique constraints
serialize nonce consumption, Cell binding and cluster binding across sessions;
Cell/cluster uniqueness intentionally survives epoch changes. It stores immutable
admission attribution/digests and consumption time, with no financial data or
credentials. It refuses reprovisioning and never adopts or clears an old registry.

This is storage preparation, not a consuming service. The separate explicit
`cell-admission-registry-grants.sql` binds `registry_database` and an externally
provisioned `service_role`. It rejects elevated/inheriting/member/owner roles and
existing forbidden table/column privileges. It grants schema USAGE, SELECT and
only admission-column INSERT, excluding `consumed_at`; no role/password is
created. Public registry and database access are revoked. Owners/superusers must
review this effect in the dedicated registry database, not a shared application
database. The future independently authenticated verifier must
verify expiry/signatures/consensus before insertion, commit before install effects
and handle exact retries via the retained digest. Owners/superusers remain trusted
and can alter the database; disaster recovery must not restore stale replay state.
Table constraints do not authenticate signatures or enforce a trustworthy clock.
The registry must live outside the Cell being installed and its rollback domain.

The opt-in `infra/tests/cell-admission-registry-postgres-test.py` requires explicit
loopback disposable PostgreSQL configuration. It checks concurrent one-winner
consumption, retained records across new sessions, conflicting Cell/cluster
reassignment across epochs and refusal to reprovision. It retains its synthetic
database; no cleanup runs. Actual PostgreSQL restart/failover, restricted service
authenticated consumption, crash/resume and whole-Cell integration remain
unqualified. Partial registry provisioning must be inspected by its owner, never
silently dropped or reset to retry installation.

Rust/Go interoperability is opt-in: set `CELL_ADMISSION_CONTRACT_VECTOR` to the
absolute Contracts-owned `test-vectors/cell-admission-v1.json` path when running
`go test -race ./...`. The test checks identical canonical payload bytes/digest
and verifies Rust-generated signatures with Go's actual pinned-roster verifier.
Without that variable the interoperability test skips; ordinary Go tests alone
do not qualify cross-repository compatibility. The vector is synthetic public
test material, never a provisioned Bank roster or release authorization.
