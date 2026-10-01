package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	cjson "github.com/cometbft/cometbft/libs/json"
	"github.com/cometbft/cometbft/light"
	ctypes "github.com/cometbft/cometbft/types"
)

type TrustAnchor struct {
	LightBlock            json.RawMessage `json:"lightBlock"`
	Policy                Policy          `json:"policy"`
	TrustingPeriodSeconds int64           `json:"trustingPeriodSeconds"`
}
type ConsensusProof struct {
	Schema              string            `json:"schema"`
	Blocks              []json.RawMessage `json:"blocks"` // Contiguous successor light blocks.
	ApprovalBlockHeight int64             `json:"approvalBlockHeight"`
	Transactions        [][]byte          `json:"transactions"`
	State               json.RawMessage   `json:"state"` // Entire committed state at approval height.
}
type ConsensusResult struct {
	Schema                 string `json:"schema"`
	ReleaseDigest          string `json:"releaseLockCanonicalDigest"`
	NetworkID              string `json:"networkId"`
	Sequence               uint64 `json:"sequence"`
	Epoch                  uint64 `json:"epoch"`
	Height                 int64  `json:"height"`
	CommitDigest           string `json:"commitDigest"`
	ApprovalDigest         string `json:"approvalDigest"`
	PreviousApprovalDigest string `json:"previousApprovalDigest"`
	VerifiedThroughHeight  int64  `json:"verifiedThroughHeight"`
	VerifiedThroughDigest  string `json:"verifiedThroughDigest"`
}

func parseLight(raw []byte, chain string) (*ctypes.LightBlock, error) {
	var block ctypes.LightBlock
	if err := cjson.Unmarshal(raw, &block); err != nil {
		return nil, err
	}
	if err := block.ValidateBasic(chain); err != nil {
		return nil, err
	}
	return &block, nil
}
func verifyConsensus(anchor TrustAnchor, proof ConsensusProof, releaseDigest string, sequence uint64, now time.Time) (*ConsensusResult, error) {
	if err := validatePolicy(anchor.Policy); err != nil {
		return nil, err
	}
	if anchor.TrustingPeriodSeconds <= 0 || anchor.TrustingPeriodSeconds > 14*24*3600 {
		return nil, errors.New("trust period must be explicitly bounded to <=14 days")
	}
	if proof.Schema != "kerosene.release-consensus-proof/v1" || len(proof.Blocks) < 2 || len(proof.Blocks) > 4096 || !hashRE.MatchString(releaseDigest) {
		return nil, errors.New("invalid proof bounds or domain")
	}
	trusted, err := parseLight(anchor.LightBlock, anchor.Policy.NetworkID)
	if err != nil {
		return nil, err
	}
	if len(trusted.ValidatorSet.Validators) != len(anchor.Policy.Members) || !bytes.Equal(trusted.NextValidatorsHash, trusted.ValidatorSet.Hash()) {
		return nil, errors.New("trusted consensus set does not match static membership size")
	}
	for _, validator := range trusted.ValidatorSet.Validators {
		if validator.VotingPower != 1 {
			return nil, errors.New("static Bank governance requires equal voting power")
		}
	}
	if err := trusted.ValidatorSet.VerifyCommitLight(anchor.Policy.NetworkID, trusted.Commit.BlockID, trusted.Height, trusted.Commit); err != nil {
		return nil, fmt.Errorf("anchor commit: %w", err)
	}
	if proof.ApprovalBlockHeight <= trusted.Height {
		return nil, errors.New("approval must follow immutable trusted anchor")
	}
	previous := trusted
	var target, next *ctypes.LightBlock
	for _, raw := range proof.Blocks {
		block, err := parseLight(raw, anchor.Policy.NetworkID)
		if err != nil {
			return nil, err
		}
		if err := light.VerifyAdjacent(previous.SignedHeader, block.SignedHeader, block.ValidatorSet, time.Duration(anchor.TrustingPeriodSeconds)*time.Second, now, 30*time.Second); err != nil {
			return nil, fmt.Errorf("consensus chain: %w", err)
		}
		// This application does not rotate membership. Proof cannot sneak in a
		// validator transition even if the underlying library supports it.
		if !bytes.Equal(block.ValidatorsHash, trusted.ValidatorsHash) || !bytes.Equal(block.NextValidatorsHash, trusted.NextValidatorsHash) {
			return nil, errors.New("implicit consensus membership rotation forbidden")
		}
		if block.Height == proof.ApprovalBlockHeight {
			target = block
		}
		if block.Height == proof.ApprovalBlockHeight+1 {
			next = block
		}
		previous = block
	}
	if target == nil || next == nil {
		return nil, errors.New("proof lacks approval and following committed app-hash header")
	}
	if previous.Height != next.Height {
		return nil, errors.New("proof must end at following app-hash header")
	}
	if len(proof.Transactions) > 10000 {
		return nil, errors.New("transaction count limit")
	}
	txs := make(ctypes.Txs, len(proof.Transactions))
	for i, tx := range proof.Transactions {
		if len(tx) > 65536 {
			return nil, errors.New("transaction byte limit")
		}
		txs[i] = ctypes.Tx(tx)
	}
	if !bytes.Equal(txs.Hash(), target.DataHash) {
		return nil, errors.New("approval block data inclusion hash mismatch")
	}
	var state State
	if err := strictDecode(proof.State, &state); err != nil {
		return nil, err
	}
	expectedPolicy, _ := json.Marshal(anchor.Policy)
	actualPolicy, _ := json.Marshal(state.Policy)
	if !bytes.Equal(expectedPolicy, actualPolicy) || state.Height != target.Height || !bytes.Equal(stateHash(state), next.AppHash) {
		return nil, errors.New("approval state is not committed by following consensus header")
	}
	var decision *Approval
	if len(state.Approvals) > 100000 {
		return nil, errors.New("approval history limit")
	}
	prior := "sha256:" + string(bytes.Repeat([]byte{'0'}, 64))
	for i, a := range state.Approvals {
		if a.Schema != ApprovalSchema || a.Sequence != uint64(i+1) || a.Epoch != anchor.Policy.Epoch || a.NetworkID != anchor.Policy.NetworkID || a.PreviousApprovalDigest != prior || !hashRE.MatchString(a.ReleaseDigest) {
			return nil, errors.New("committed approval history is invalid")
		}
		prior = approvalDigest(a)
		if a.Sequence == sequence && a.ReleaseDigest == releaseDigest {
			copy := a
			decision = &copy
		}
	}
	if decision == nil {
		return nil, errors.New("requested release was not successfully approved in committed state")
	}
	found := false
	for _, tx := range proof.Transactions {
		var p Proposal
		if strictDecode(tx, &p) == nil && p.Approval == *decision {
			found = true
			break
		}
	}
	if !found {
		return nil, errors.New("requested approval is absent from this block's transactions")
	}
	return &ConsensusResult{Schema: "kerosene.release-consensus-verification/v1", ReleaseDigest: releaseDigest, NetworkID: anchor.Policy.NetworkID, Sequence: sequence, Epoch: anchor.Policy.Epoch, Height: target.Height, CommitDigest: "sha256:" + hex.EncodeToString(target.Hash()), ApprovalDigest: approvalDigest(*decision), PreviousApprovalDigest: decision.PreviousApprovalDigest, VerifiedThroughHeight: next.Height, VerifiedThroughDigest: "sha256:" + hex.EncodeToString(next.Hash())}, nil
}

func boundedRead(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > 8*1024*1024 {
		return nil, errors.New("proof input must be a bounded regular file")
	}
	return os.ReadFile(path)
}
