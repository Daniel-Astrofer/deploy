// Package main implements a separate release-approval state machine, not the
// financial ledger. CometBFT drives ordering; application signatures authorize
// proposals but are never substituted for a consensus commit.
package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sync"

	abci "github.com/cometbft/cometbft/abci/types"
)

const ApprovalSchema = "kerosene.release-approval/v1"
const maxExactJSONInteger uint64 = 9007199254740991

var hashRE = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
var nameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{2,127}$`)

// Field order is alphabetical, matching the restricted canonical JSON used by
// the release controller. Strings are bounded ASCII identifiers/digests.
type Approval struct {
	Epoch                  uint64 `json:"epoch"`
	NetworkID              string `json:"networkId"`
	PreviousApprovalDigest string `json:"previousApprovalDigest"`
	ReleaseDigest          string `json:"releaseLockCanonicalDigest"`
	Schema                 string `json:"schema"`
	Sequence               uint64 `json:"sequence"`
}
type ProposalSignature struct {
	MemberID  string `json:"memberId"`
	Signature string `json:"signatureBase64"`
}
type Proposal struct {
	Approval   Approval            `json:"approval"`
	Signatures []ProposalSignature `json:"signatures"`
}
type Policy struct {
	NetworkID string            `json:"networkId"`
	Epoch     uint64            `json:"epoch"`
	Threshold int               `json:"threshold"`
	Members   map[string]string `json:"members"` // Raw Ed25519 public key base64.
}
type State struct {
	Height    int64      `json:"height"`
	Policy    Policy     `json:"policy"`
	Approvals []Approval `json:"approvals"`
}

func hashBytes(raw []byte) string {
	h := sha256.Sum256(raw)
	return "sha256:" + hex.EncodeToString(h[:])
}
func approvalDigest(a Approval) string { raw, _ := json.Marshal(a); return hashBytes(raw) }
func stateBytes(s State) []byte        { raw, _ := json.Marshal(s); return raw }
func stateHash(s State) []byte         { h := sha256.Sum256(stateBytes(s)); return h[:] }

// Strict JSON prevents ambiguities in a consensus input. Duplicate fields are
// rejected recursively, not merely ignored by encoding/json.
func strictDecode(raw []byte, out any) error {
	if len(raw) > 8*1024*1024 {
		return errors.New("input limit")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	depth := 0
	var scan func() error
	scan = func() error {
		depth++
		defer func() { depth-- }()
		if depth > 128 {
			return errors.New("JSON nesting limit")
		}
		token, err := d.Token()
		if err != nil {
			return err
		}
		delimiter, is := token.(json.Delim)
		if !is {
			return nil
		}
		switch delimiter {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return err
				}
				k, ok := key.(string)
				if !ok || seen[k] {
					return errors.New("duplicate JSON key")
				}
				seen[k] = true
				if err := scan(); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return errors.New("invalid object")
			}
		case '[':
			for d.More() {
				if err := scan(); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return errors.New("invalid array")
			}
		default:
			return errors.New("invalid delimiter")
		}
		return nil
	}
	if err := scan(); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing input")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	return decoder.Decode(out)
}

func validatePolicy(p Policy) error {
	if !nameRE.MatchString(p.NetworkID) || p.Epoch == 0 || p.Epoch > maxExactJSONInteger || len(p.Members) < 4 || len(p.Members) > 64 || p.Threshold < 2*len(p.Members)/3+1 || p.Threshold > len(p.Members) {
		return errors.New("invalid static governance policy")
	}
	keys := map[string]bool{}
	for member, key := range p.Members {
		raw, e := base64.StdEncoding.DecodeString(key)
		if !nameRE.MatchString(member) || e != nil || len(raw) != ed25519.PublicKeySize || keys[key] {
			return errors.New("invalid or duplicate proposer identity")
		}
		keys[key] = true
	}
	return nil
}
func validateProposal(s State, p Proposal) error {
	a := p.Approval
	previous := "sha256:" + string(bytes.Repeat([]byte{'0'}, 64))
	sequence := uint64(1)
	if len(s.Approvals) > 0 {
		last := s.Approvals[len(s.Approvals)-1]
		if last.Sequence >= maxExactJSONInteger {
			return errors.New("approval sequence exhausted")
		}
		previous = approvalDigest(last)
		sequence = last.Sequence + 1
	}
	if a.Schema != ApprovalSchema || a.NetworkID != s.Policy.NetworkID || a.Epoch == 0 || a.Epoch > maxExactJSONInteger || a.Epoch != s.Policy.Epoch || a.Sequence == 0 || a.Sequence > maxExactJSONInteger || a.Sequence != sequence || a.PreviousApprovalDigest != previous || !hashRE.MatchString(a.ReleaseDigest) {
		return errors.New("proposal domain, sequence or predecessor mismatch")
	}
	if len(p.Signatures) > len(s.Policy.Members) {
		return errors.New("signature set limit")
	}
	message, _ := json.Marshal(a)
	seen := map[string]bool{}
	for _, signature := range p.Signatures {
		encoded, ok := s.Policy.Members[signature.MemberID]
		if !ok || seen[signature.MemberID] {
			return errors.New("unknown or duplicate proposer")
		}
		key, _ := base64.StdEncoding.DecodeString(encoded)
		sig, err := base64.StdEncoding.DecodeString(signature.Signature)
		if err != nil || !ed25519.Verify(key, message, sig) {
			return errors.New("invalid proposer signature")
		}
		seen[signature.MemberID] = true
	}
	if len(seen) < s.Policy.Threshold {
		return errors.New("insufficient proposal authorizers")
	}
	return nil
}

type Application struct {
	abci.BaseApplication
	mu        sync.Mutex
	committed State
	pending   *State
	path      string
}

func newApplication(path string, policy Policy) (*Application, error) {
	if err := validatePolicy(policy); err != nil {
		return nil, err
	}
	a := &Application{path: path, committed: State{Policy: policy, Approvals: []Approval{}}}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return a, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("state must be private regular file")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if err := strictDecode(raw, &a.committed); err != nil {
		return nil, err
	}
	expected, _ := json.Marshal(policy)
	actual, _ := json.Marshal(a.committed.Policy)
	if !bytes.Equal(expected, actual) {
		return nil, errors.New("genesis policy changed; static membership forbids implicit rotation")
	}
	prior := State{Policy: policy, Approvals: []Approval{}}
	for _, approval := range a.committed.Approvals {
		previous := "sha256:" + string(bytes.Repeat([]byte{'0'}, 64))
		if len(prior.Approvals) > 0 {
			previous = approvalDigest(prior.Approvals[len(prior.Approvals)-1])
		}
		if approval.Sequence != uint64(len(prior.Approvals)+1) || approval.PreviousApprovalDigest != previous || approval.NetworkID != policy.NetworkID || approval.Epoch != policy.Epoch || approval.Schema != ApprovalSchema || !hashRE.MatchString(approval.ReleaseDigest) {
			return nil, errors.New("corrupt persisted approval chain")
		}
		prior.Approvals = append(prior.Approvals, approval)
	}
	return a, nil
}
func (a *Application) Info(context.Context, *abci.RequestInfo) (*abci.ResponseInfo, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return &abci.ResponseInfo{Data: "kerosene-release-governance/v1", Version: "0.1.0", AppVersion: 1, LastBlockHeight: a.committed.Height, LastBlockAppHash: stateHash(a.committed)}, nil
}
func (a *Application) InitChain(_ context.Context, r *abci.RequestInitChain) (*abci.ResponseInitChain, error) {
	if r.ChainId != a.committed.Policy.NetworkID {
		return nil, errors.New("genesis consensus chain ID differs from approved policy")
	}
	return &abci.ResponseInitChain{AppHash: stateHash(a.committed)}, nil
}
func (a *Application) Query(_ context.Context, r *abci.RequestQuery) (*abci.ResponseQuery, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if r.Path != "/release/state" {
		return &abci.ResponseQuery{Code: 1, Log: "unsupported read"}, nil
	}
	return &abci.ResponseQuery{Code: 0, Height: a.committed.Height, Value: stateBytes(a.committed)}, nil
}
func (a *Application) CheckTx(_ context.Context, r *abci.RequestCheckTx) (*abci.ResponseCheckTx, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	var p Proposal
	if len(r.Tx) > 65536 || strictDecode(r.Tx, &p) != nil || validateProposal(a.committed, p) != nil {
		return &abci.ResponseCheckTx{Code: 1, Log: "release proposal rejected"}, nil
	}
	return &abci.ResponseCheckTx{Code: 0}, nil
}
func (a *Application) FinalizeBlock(_ context.Context, r *abci.RequestFinalizeBlock) (*abci.ResponseFinalizeBlock, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if r.Height != a.committed.Height+1 {
		return nil, errors.New("nonsequential consensus height")
	}
	next := a.committed
	next.Approvals = append([]Approval{}, a.committed.Approvals...)
	next.Height = r.Height
	results := make([]*abci.ExecTxResult, 0, len(r.Txs))
	for _, tx := range r.Txs {
		var p Proposal
		result := &abci.ExecTxResult{Code: 1, Log: "release proposal rejected"}
		if len(tx) <= 65536 && strictDecode(tx, &p) == nil && validateProposal(next, p) == nil {
			next.Approvals = append(next.Approvals, p.Approval)
			result = &abci.ExecTxResult{Code: 0}
		}
		results = append(results, result)
	}
	a.pending = &next
	return &abci.ResponseFinalizeBlock{TxResults: results, AppHash: stateHash(next)}, nil
}
func (a *Application) Commit(context.Context, *abci.RequestCommit) (*abci.ResponseCommit, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.pending == nil {
		return nil, errors.New("commit without finalized block")
	}
	if err := persist(a.path, stateBytes(*a.pending)); err != nil {
		return nil, err
	}
	a.committed = *a.pending
	a.pending = nil
	return &abci.ResponseCommit{}, nil
}
func persist(path string, raw []byte) error {
	parent := filepath.Dir(path)
	info, err := os.Lstat(parent)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0022 != 0 {
		return errors.New("state parent must be protected real directory")
	}
	f, err := os.CreateTemp(parent, ".release-state-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if err = f.Chmod(0600); err != nil {
		return err
	}
	if _, err = f.Write(raw); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = os.Rename(f.Name(), path); err != nil {
		return err
	}
	dir, err := os.Open(parent)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
func fail(err error) { fmt.Fprintln(os.Stderr, "release governance rejected:", err); os.Exit(78) }
