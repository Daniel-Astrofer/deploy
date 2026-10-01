package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
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
	if _, err := verifyConsensus(anchor, proof, "sha256:"+strings.Repeat("b", 64), 1, now); err == nil {
		t.Fatal("wrong release accepted")
	}
	original := proof.State
	state.Approvals[0].ReleaseDigest = "sha256:" + strings.Repeat("b", 64)
	proof.State = stateBytes(state)
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
