# Ordered release governance v3

The implementation in `infra/governance` is a separate static-membership
CometBFT ABCI application for release authorization. It does not alter the
financial ledger or convert detached compatibility signatures into consensus.

An approval binds network, epoch, target sequence, full canonical release-lock
digest and predecessor approval digest. Proposal authorizers sign the entire
approval before submission. Each validator independently validates proposer
policy and the durable predecessor/sequence chain. FinalizeBlock deterministically
accepts valid sequential approvals; Commit persists the resulting state.
CheckTx success alone is not approval. Restart revalidates the persisted chain
and exact configured policy rather than resetting sequence state.

The v3 release lock omits its future block height/hash. Including that hash
would be circular: lock digest -> approval transaction -> block hash -> lock.
The external proof supplies the resulting block identity instead.

The verifier starts from an independently provisioned light-block anchor,
bounded trust period and authorizer policy. It verifies contiguous adjacent
light blocks, real >2/3 validator voting-power commits, the approval transaction
under the committed data root, the complete ordered state at that height, and
its application hash committed by the following block. It then binds the exact
requested target digest and sequence. The controller independently checks the
anchor policy against its protected release roster and pins the verifier
executable. It does not trust validator keys supplied by the proof itself.

Current supported membership is four distinct equal-power validators with
three-of-four proposal authorizers. Static validator membership is enforced;
key/membership rotation and a dynamic epoch transition are not implemented.
Authorizer identities and consensus-validator identities are separate. A
bootstrap anchor must be independently checked against the intended network.
An expired anchor cannot be refreshed by an untrusted mirror alone.

The ABCI socket is private Unix-only. JSON parsing rejects duplicate/unknown
fields, noninteroperable numbers and oversized/deep documents. Application
state is private and durably flushed. The proof is public data, never executable
release content. A compromised trust-anchor provisioner or verifier installer
is outside the protection supplied by release signatures.

`infra/governance/testnet-test.sh` exercises four real CometBFT processes:
four online commit; one unavailable still commits; two unavailable do not
commit; restoring the third restores progress. It rejects altered application
state/transactions, minority commits, detached proposal signatures and a target
that was not ordered. Tests use isolated synthetic keys and are not operational
release evidence. See [testnet runbook](../operations/release-governance-testnet.md).
