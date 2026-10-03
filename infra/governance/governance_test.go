package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	abci "github.com/cometbft/cometbft/abci/types"
	ccrypto "github.com/cometbft/cometbft/crypto"
	ced25519 "github.com/cometbft/cometbft/crypto/ed25519"
	cjson "github.com/cometbft/cometbft/libs/json"
	proto "github.com/cometbft/cometbft/proto/tendermint/types"
	version "github.com/cometbft/cometbft/proto/tendermint/version"
	ctypes "github.com/cometbft/cometbft/types"
)

func testPolicy(t *testing.T) (Policy, map[string]ed25519.PrivateKey) {
	t.Helper()
	p := Policy{NetworkID: "bank-release-governance", Epoch: 1, Threshold: 3, Members: map[string]string{}}
	keys := map[string]ed25519.PrivateKey{}
	for _, member := range []string{"validator-1", "validator-2", "validator-3", "validator-4"} {
		pub, priv, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		p.Members[member] = base64.StdEncoding.EncodeToString(pub)
		keys[member] = priv
	}
	return p, keys
}
func proposal(keys map[string]ed25519.PrivateKey, a Approval) Proposal {
	raw, _ := json.Marshal(a)
	p := Proposal{Approval: a, Signatures: []ProposalSignature{}}
	for _, member := range []string{"validator-1", "validator-2", "validator-3"} {
		p.Signatures = append(p.Signatures, ProposalSignature{MemberID: member, Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(keys[member], raw))})
	}
	return p
}
func TestOrderedDurableApproval(t *testing.T) {
	policy, keys := testPolicy(t)
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.Chmod(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	app, err := newApplication(path, policy)
	if err != nil {
		t.Fatal(err)
	}
	a := Approval{Schema: ApprovalSchema, Epoch: 1, NetworkID: policy.NetworkID, Sequence: 1, ReleaseDigest: "sha256:" + strings.Repeat("a", 64), PreviousApprovalDigest: "sha256:" + strings.Repeat("0", 64)}
	tx, _ := json.Marshal(proposal(keys, a))
	result, err := app.FinalizeBlock(context.Background(), &abci.RequestFinalizeBlock{Height: 1, Txs: [][]byte{tx, tx}})
	if err != nil {
		t.Fatal(err)
	}
	if result.TxResults[0].Code != 0 || result.TxResults[1].Code == 0 {
		t.Fatal("same sequence accepted twice")
	}
	if _, err := app.Commit(context.Background(), &abci.RequestCommit{}); err != nil {
		t.Fatal(err)
	}
	restarted, err := newApplication(path, policy)
	if err != nil {
		t.Fatal(err)
	}
	if len(restarted.committed.Approvals) != 1 || restarted.committed.Height != 1 {
		t.Fatal("durability")
	}
	check, _ := restarted.CheckTx(context.Background(), &abci.RequestCheckTx{Tx: tx})
	if check.Code == 0 {
		t.Fatal("replay admitted")
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("private state permissions")
	}
	policy.Epoch = 2
	if _, err := newApplication(path, policy); err == nil {
		t.Fatal("implicit epoch rotation")
	}
}
func TestProposalCannotSelfAuthorize(t *testing.T) {
	policy, keys := testPolicy(t)
	state := State{Policy: policy}
	a := Approval{Schema: ApprovalSchema, Epoch: 1, NetworkID: policy.NetworkID, Sequence: 1, ReleaseDigest: "sha256:" + strings.Repeat("a", 64), PreviousApprovalDigest: "sha256:" + strings.Repeat("0", 64)}
	p := proposal(keys, a)
	p.Signatures = p.Signatures[:2]
	if validateProposal(state, p) == nil {
		t.Fatal("minority")
	}
	p = proposal(keys, a)
	p.Signatures[2] = p.Signatures[0]
	if validateProposal(state, p) == nil {
		t.Fatal("duplicate voter")
	}
	p = proposal(keys, a)
	p.Approval.NetworkID = "foreign-bank"
	if validateProposal(state, p) == nil {
		t.Fatal("foreign domain")
	}
	if strictDecode([]byte(`{"epoch":1,"epoch":2}`), &a) == nil {
		t.Fatal("duplicate JSON")
	}
}

func TestGovernanceContractBounds(t *testing.T) {
	policy, keys := testPolicy(t)
	policy.Epoch = maxExactJSONInteger
	if err := validatePolicy(policy); err != nil {
		t.Fatal("maximum exact epoch rejected", err)
	}
	for _, epoch := range []uint64{0, maxExactJSONInteger + 1, ^uint64(0)} {
		policy.Epoch = epoch
		if validatePolicy(policy) == nil {
			t.Fatal("out-of-contract epoch accepted", epoch)
		}
	}
	policy.Epoch = 1
	for len(policy.Members) < 65 {
		pub, _, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		policy.Members[fmt.Sprintf("validator-%d", len(policy.Members)+1)] = base64.StdEncoding.EncodeToString(pub)
		policy.Threshold = 2*len(policy.Members)/3 + 1
		if len(policy.Members) == 64 && validatePolicy(policy) != nil {
			t.Fatal("64-member policy rejected")
		}
	}
	if validatePolicy(policy) == nil {
		t.Fatal("65-member policy accepted")
	}
	policy, keys = testPolicy(t)
	for _, sequence := range []uint64{0, maxExactJSONInteger + 1, ^uint64(0)} {
		_, err := verifyConsensus(TrustAnchor{Policy: policy, TrustingPeriodSeconds: 60},
			ConsensusProof{Schema: "kerosene.release-consensus-proof/v1", Blocks: []json.RawMessage{nil, nil}},
			"sha256:"+strings.Repeat("a", 64), sequence, time.Now())
		if err == nil || err.Error() != "invalid proof bounds or domain" {
			t.Fatal("out-of-contract expected sequence reached proof parsing", sequence, err)
		}
	}
	// This synthetic terminal state tests the boundary without allocating an
	// impossible number of approvals. No restart/chain qualification is implied.
	last := Approval{Sequence: maxExactJSONInteger - 1}
	a := Approval{Schema: ApprovalSchema, Epoch: 1, NetworkID: policy.NetworkID,
		Sequence: maxExactJSONInteger, ReleaseDigest: "sha256:" + strings.Repeat("a", 64),
		PreviousApprovalDigest: approvalDigest(last)}
	if err := validateProposal(State{Policy: policy, Approvals: []Approval{last}}, proposal(keys, a)); err != nil {
		t.Fatal("last exact sequence rejected", err)
	}
	for _, terminal := range []uint64{maxExactJSONInteger, ^uint64(0)} {
		last.Sequence = terminal
		a.Sequence = terminal + 1
		a.PreviousApprovalDigest = approvalDigest(last)
		if validateProposal(State{Policy: policy, Approvals: []Approval{last}}, proposal(keys, a)) == nil {
			t.Fatal("sequence exhaustion or overflow admitted")
		}
	}
}

func signedBlock(t *testing.T, height int64, stamp time.Time, vals *ctypes.ValidatorSet, keys map[string]ccrypto.PrivKey, appHash []byte, txs ctypes.Txs, previous *ctypes.LightBlock, signers int) *ctypes.LightBlock {
	t.Helper()
	header := &ctypes.Header{Version: version.Consensus{Block: 11, App: 1}, ChainID: "bank-release-governance", Height: height, Time: stamp, ValidatorsHash: vals.Hash(), NextValidatorsHash: vals.Hash(), AppHash: appHash, DataHash: txs.Hash(), ProposerAddress: vals.Validators[0].Address}
	if previous != nil {
		header.LastBlockID = previous.Commit.BlockID
		header.LastCommitHash = previous.Commit.Hash()
	}
	id := ctypes.BlockID{Hash: header.Hash(), PartSetHeader: ctypes.PartSetHeader{Total: 1, Hash: make([]byte, 32)}}
	signatures := make([]ctypes.CommitSig, len(vals.Validators))
	for i, val := range vals.Validators {
		if i >= signers {
			signatures[i] = ctypes.NewCommitSigAbsent()
			continue
		}
		vote := &ctypes.Vote{Type: proto.PrecommitType, Height: height, Round: 0, BlockID: id, Timestamp: stamp, ValidatorAddress: val.Address, ValidatorIndex: int32(i)}
		sig, err := keys[string(val.Address)].Sign(ctypes.VoteSignBytes(header.ChainID, vote.ToProto()))
		if err != nil {
			t.Fatal(err)
		}
		vote.Signature = sig
		signatures[i] = vote.CommitSig()
	}
	return &ctypes.LightBlock{SignedHeader: &ctypes.SignedHeader{Header: header, Commit: &ctypes.Commit{Height: height, Round: 0, BlockID: id, Signatures: signatures}}, ValidatorSet: vals}
}

func TestRealConsensusSignaturesAndCommittedAppState(t *testing.T) {
	policy, authorizers := testPolicy(t)
	validators := []*ctypes.Validator{}
	keys := map[string]ccrypto.PrivKey{}
	for i := 0; i < 4; i++ {
		key := ced25519.GenPrivKey()
		validators = append(validators, ctypes.NewValidator(key.PubKey(), 1))
		keys[string(key.PubKey().Address())] = key
	}
	vals := ctypes.NewValidatorSet(validators)
	now := time.Now().UTC()
	a := Approval{Schema: ApprovalSchema, Epoch: 1, NetworkID: policy.NetworkID, Sequence: 1, ReleaseDigest: "sha256:" + strings.Repeat("a", 64), PreviousApprovalDigest: "sha256:" + strings.Repeat("0", 64)}
	tx, _ := json.Marshal(proposal(authorizers, a))
	state := State{Height: 2, Policy: policy, Approvals: []Approval{a}}
	anchorBlock := signedBlock(t, 1, now.Add(-3*time.Minute), vals, keys, []byte{}, nil, nil, 4)
	target := signedBlock(t, 2, now.Add(-2*time.Minute), vals, keys, []byte{}, ctypes.Txs{ctypes.Tx(tx)}, anchorBlock, 3)
	following := signedBlock(t, 3, now.Add(-time.Minute), vals, keys, stateHash(state), nil, target, 3)
	encode := func(block *ctypes.LightBlock) json.RawMessage {
		raw, err := cjson.Marshal(block)
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	anchor := TrustAnchor{LightBlock: encode(anchorBlock), Policy: policy, TrustingPeriodSeconds: 3600}
	proof := ConsensusProof{Schema: "kerosene.release-consensus-proof/v1", Blocks: []json.RawMessage{encode(target), encode(following)}, ApprovalBlockHeight: 2, Transactions: [][]byte{tx}, State: stateBytes(state)}
	verified, err := verifyConsensus(anchor, proof, a.ReleaseDigest, 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if verified.Height != 2 || verified.Sequence != 1 {
		t.Fatal("wrong verified decision")
	}
	admission := CellAdmission{Schema: "kerosene.cell-admission/v1", NetworkID: policy.NetworkID, Epoch: policy.Epoch,
		CellID: "cell-example", ClusterUID: "80cf8d2f-172d-4d43-ba99-1734b32184b1", OperatorID: "operator-example", ChangeID: "change-example",
		ApprovalDigest: verified.ApprovalDigest, Nonce: strings.Repeat("c", 64), IssuedAt: uint64(now.Unix()), ExpiresAt: uint64(now.Unix()) + 60}
	signAdmission := func(payload CellAdmission) []byte {
		message, _ := json.Marshal(payload)
		envelope := CellAdmissionEnvelope{Admission: payload}
		for _, member := range []string{"validator-1", "validator-2", "validator-3"} {
			envelope.Signatures = append(envelope.Signatures, ProposalSignature{member, base64.StdEncoding.EncodeToString(ed25519.Sign(authorizers[member], message))})
		}
		raw, _ := json.Marshal(envelope)
		return raw
	}
	binding := AdmissionBinding{CellID: admission.CellID, ClusterUID: admission.ClusterUID, OperatorID: admission.OperatorID, ChangeID: admission.ChangeID,
		ApprovalDigest: "sha256:" + strings.Repeat("f", 64)}
	combined, err := verifyOrderedCellAdmission(anchor, proof, a.ReleaseDigest, 1, signAdmission(admission), binding, now)
	if err != nil || combined.InstallAuthorized || combined.NonceConsumed {
		t.Fatal("read-only combined verification failed", err)
	}
	admission.ApprovalDigest = binding.ApprovalDigest
	if _, err := verifyOrderedCellAdmission(anchor, proof, a.ReleaseDigest, 1, signAdmission(admission), binding, now); err == nil {
		t.Fatal("caller-selected approval digest bypassed consensus binding")
	}
	admission.ApprovalDigest = verified.ApprovalDigest
	testOrderedAdmissionConsumptionPostgres(t, anchor, proof, a.ReleaseDigest, signAdmission(admission), binding, now)
	if _, err := verifyConsensus(anchor, proof, "sha256:"+strings.Repeat("b", 64), 1, now); err == nil {
		t.Fatal("wrong release accepted")
	}
	original := proof.State
	state.Approvals[0].ReleaseDigest = "sha256:" + strings.Repeat("b", 64)
	proof.State = stateBytes(state)
	if _, err := verifyOrderedCellAdmission(anchor, proof, a.ReleaseDigest, 1, signAdmission(admission), binding, now); err == nil {
		t.Fatal("signed admission bypassed invalid consensus state")
	}
	if _, err := verifyConsensus(anchor, proof, a.ReleaseDigest, 1, now); err == nil {
		t.Fatal("uncommitted app state")
	}
	proof.State = original
	weak := signedBlock(t, 3, now.Add(-time.Minute), vals, keys, stateHash(State{Height: 2, Policy: policy, Approvals: []Approval{a}}), nil, target, 2)
	proof.Blocks[1] = encode(weak)
	if _, err := verifyConsensus(anchor, proof, a.ReleaseDigest, 1, now); err == nil {
		t.Fatal("minority consensus accepted")
	}
}
