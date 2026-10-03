# Disposable release-governance consensus integration

`infra/governance/testnet-test.sh` starts four **real CometBFT v0.38.17 node
processes**, each connected to its own Go ABCI server over a private UNIX
socket. It is an integration test for the release-approval application, not a
financial Cell, production Bank network, trust bootstrap, or deployment.
It never reads or writes an existing Cell directory and never publishes,
signs, or promotes a real release. Every release digest is synthetic.

The harness creates four equal-power consensus validators and four separate
Ed25519 proposal-authorizer identities. It checks that the two key sets are
disjoint. Three authorizers sign each proposal, independently of which
consensus node is online. Proposal signatures admit transactions; actual
CometBFT commits, signed headers, validator sets, transaction inclusion and
committed application state establish the tested consensus decision.
Detached authorizer signatures are explicitly tested as insufficient proof.

## Run

Requirements: Linux, Bash, Python 3.11+ standard library, Go, OpenSSL with
Ed25519 `pkeyutl -sign -rawin`, and GNU `timeout`. No Docker, kubectl, live
cluster, external RPC provider or Python cryptography package is needed.

```bash
bash infra/governance/testnet-test.sh
```

The default builds the application with `go build -mod=readonly` and installs
the pinned consensus binary using
`go install github.com/cometbft/cometbft/cmd/cometbft@v0.38.17`. Build/install
steps have a five-minute bound each. The application module and both linked
binaries must use v0.38.17. Go downloads dependencies through its normal
module/checksum verification; it does not modify the governance source files.

You can supply prebuilt binaries to avoid repeating builds:

```bash
GOVERNANCE_BINARY=/absolute/path/kerosene-release-consensus \
COMETBFT_BINARY=/absolute/path/cometbft \
KEEP_EVIDENCE=1 \
  bash infra/governance/testnet-test.sh
```

`go version -m` must report the pinned CometBFT module for each binary, and
`cometbft version` must report exactly `0.38.17`. Use freshly built application
code when assessing current source changes. A supplied binary is operator
selected; the test does not infer that it matches the working tree.

All consensus/app homes, policy, generated keys, sockets and logs live under
an invocation-owned `mktemp -d /tmp/kerosene-governance-testnet.XXXXXX` tree,
with umask `077` and private state/socket directories. RPC and P2P listeners
bind only `127.0.0.1` on allocated ephemeral ports. There are no seeds, peer
discovery, remote listeners, validator membership changes or unsafe RPC.
The chain ID/network is `bank-release-governance`, epoch 1; proposal policy
is static three-of-four. Timestamps come from the actual running test, with
a 600-second laboratory anchor trust period. Consensus scenarios have a
240-second overall deadline and bounded readiness, RPC and verifier calls.

## Assertions and evidence

The test performs these scenarios against live processes:

1. Wait for all four nodes to connect and commit blocks. Select a signed
   immutable laboratory anchor **before submitting any approval**.
2. Submit a three-authorizer proposal for sequence 1 and wait for its actual
   consensus transaction result and the following signed header. Export its
   proof and run the installed Go offline verifier successfully.
3. Reject state tampering, transaction tampering, a following commit reduced
   to two votes, and a detached authorizer proposal presented as a proof.
4. Stop the fourth CometBFT node and its ABCI server. Submit sequence 2,
   requiring the real approval commit to contain exactly three votes. Export
   and verify the decision while one validator is offline.
5. Stop the third node as well. After in-flight work drains, submit a valid
   three-authorizer sequence-3 proposal. `CheckTx` accepts it, but the two
   remaining nodes must preserve committed height and two-approval history
   throughout an eight-second observation. The offline verifier must also
   reject sequence 3 when presented with the prior committed proof.
6. Restart the third node with its existing **disposable** persisted state.
   The queued sequence-3 transaction must commit when three-of-four consensus
   voting power returns, and its real proof must verify.

The two-node result is a bounded liveness observation, not a proof about an
infinite outage. Quorum restoration demonstrates that the pending proposal
was valid and the failure to approve came from insufficient consensus voting
power. The test does not simulate Byzantine equivocation, network partitions,
key compromise, production trust distribution, or financial correctness.

Successful stdout includes a JSON
`kerosene.release-governance-testnet-result/v1` report with actual heights,
commit vote counts, verified approval/commit digests, failure reasons,
elapsed time and SHA-256 hashes of exported positive proofs. These values
change between runs because validator keys and live consensus timestamps are
fresh. Negative tests pass only when the verifier returns nonzero.

By default all temporary data is removed after owned children exit. With
`KEEP_EVIDENCE=1`, only public synthetic policy/anchor/proof/verification/result
files are copied to a new private
`/tmp/kerosene-governance-evidence.XXXXXX` directory whose path is printed.
Generated private keys, node homes, application sockets and app states are
never copied to this retained evidence directory. Evidence remains laboratory
data and must not be used as an operator's real trusted anchor.

## Untrusted RPC proof exporter

`infra/governance/export-proof.py` has two commands:

```bash
python3 infra/governance/export-proof.py anchor \
  --rpc http://127.0.0.1:26657 --height 2 \
  --policy /private/lab/policy.json --trusting-period 600 \
  --output /private/lab/anchor.json

python3 infra/governance/export-proof.py proof \
  --rpc http://127.0.0.1:26657 \
  --trusted-anchor /private/lab/anchor.json --approval-height 3 \
  --output /private/lab/proof.json

/absolute/path/kerosene-release-consensus verify \
  --trusted-anchor /private/lab/anchor.json --proof /private/lab/proof.json \
  --release-digest "sha256:$SYNTHETIC_RELEASE_HEX" --sequence 1
```

Example ports/heights/paths are placeholders. The output directory must
already exist, be owned by the operator, and have mode `0700`. Outputs are
exclusive atomic publications: files and parent directories are fsynced,
existing outputs are not overwritten. JSON inputs/responses/proofs are bounded
to 8 MiB; duplicate keys and nonintegral/nonfinite numbers are rejected.
The exporter accepts only explicit numeric loopback HTTP hosts/ports, disables
proxies and redirects, and bounds RPC requests/export duration. It deliberately
does not treat TLS or the RPC provider as proof authenticity.

Python import contracts:

```python
RPC("http://127.0.0.1:PORT", timeout=3, total_seconds=90)
export_anchor(rpc, height, policy, trusting_period=600) -> dict
export_proof(rpc, anchor, target_height) -> dict
```

`anchor` exports an actual `/commit` signed header plus `/validators` at the
selected height into the Go `TrustAnchor` shape:
`{lightBlock:{signed_header,validator_set},policy,trustingPeriodSeconds}`.
The validator-set proposer is taken from the header's proposer address,
with all four equal-power validators in RPC order. Export does **not** verify
their signatures. Only a disposable laboratory may select this RPC-provided
anchor directly. A real operator must provision an independently approved
anchor out of band. The anchor command never installs it into a controller.

`proof` exports `kerosene.release-consensus-proof/v1` with `blocks`,
`approvalBlockHeight`, `transactions` and `state`, matching `verify.go`:

| Material | Source and binding |
| --- | --- |
| Successor light blocks | Actual signed `/commit` and `/validators`, contiguous from anchor+1 through target+1 |
| Target transactions | Exact base64 transaction bytes from `/block` at target height |
| Approval-height state | Reconstructed from successful `/block_results` and matching `/block` transactions from genesis through target |
| Latest application history | `/abci_query` at `/release/state`, used to cross-check the reconstructed prefix/policy |
| Target state commitment | SHA-256 of Go-compatible State/Policy serialization, matched to following signed header `app_hash` |

History is bounded to heights below 4096. The static app does not maintain a
historical query store, and each empty block advances `State.height`.
Consequently the exporter reconstructs target-height state rather than
claiming a later query is an exact snapshot. It includes only successful
application transactions, checks sequence and current committed history, and
matches the state hash against the next signed header. This follows the
[CometBFT v0.38.17 ABCI contract](https://github.com/cometbft/cometbft/blob/v0.38.17/spec/abci/abci%2B%2B_methods.md):
`FinalizeBlock.app_hash` is carried by the following block header.

The reconstruction check is a transport/data consistency check. The installed
Go verifier remains responsible for anchor signatures, adjacent light-block
verification, actual voting-power quorum, immutable membership, expiry/time,
block-data inclusion, state commitment and approval domain/sequence/digest.
Exporter stdout always reports `cryptographicallyVerified:false`.
It does not emit a trusted release authorization or a detached-signature
receipt masquerading as a BFT commit.

## Failure, cleanup and recovery

The application and offline verifier enforce the Contracts numeric domain:
epoch and approval sequence are 1 through 9007199254740991, with 4 through
64 distinct policy members. Sequence exhaustion is rejected before increment,
including uint64 overflow. Restart refuses an out-of-domain policy; do not edit
persisted consensus state to bypass this check. A legacy policy outside these
limits requires explicit governance migration, not automatic normalization.

Failed runs exit nonzero and print tails of only their own synthetic process
logs. Teardown signals and waits for the exact `Popen` children created by
that invocation, stopping Comet before its ABCI app. It does not use `pkill`,
`killall`, port-based killing, or broad process matching. Recursive removal is
restricted to the exact fresh invocation-owned test directory; public evidence
can optionally be retained as above.

No deployment rollback is required: the test changes only disposable local
state. Retry a failed test with a fresh directory and check binary versions,
socket permissions, available ports and logs. Do not change real trust roots,
release approvals, validator keys, Cell state or Vault signer activation to
recover a laboratory run.
