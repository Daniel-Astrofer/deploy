package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"regexp"
	"time"
)

// Alphabetical field order matches Contracts canonical admission JSON.
type CellAdmission struct {
	CellID         string `json:"cellId"`
	ChangeID       string `json:"changeId"`
	ClusterUID     string `json:"clusterUid"`
	Epoch          uint64 `json:"epoch"`
	ExpiresAt      uint64 `json:"expiresAtUnixSeconds"`
	IssuedAt       uint64 `json:"issuedAtUnixSeconds"`
	NetworkID      string `json:"networkId"`
	Nonce          string `json:"nonce"`
	OperatorID     string `json:"operatorId"`
	ApprovalDigest string `json:"releaseApprovalDigest"`
	Schema         string `json:"schema"`
}
type CellAdmissionEnvelope struct {
	Admission  CellAdmission       `json:"admission"`
	Signatures []ProposalSignature `json:"signatures"`
}
type AdmissionBinding struct{ CellID, ClusterUID, OperatorID, ChangeID, ApprovalDigest string }

var clusterUIDRE = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var admissionNonceRE = regexp.MustCompile(`^[0-9a-f]{64}$`)

// Verifies quorum authorization only. The caller must derive policy and approval
// digest from trusted consensus verification, authenticate the operator, read
// the live cluster UID and atomically consume the nonce before any effects.
// No install path calls this until authoritative replay protection exists.
func verifyCellAdmission(policy Policy, raw []byte, expected AdmissionBinding, now uint64) (string, error) {
	if err := validatePolicy(policy); err != nil {
		return "", err
	}
	var envelope CellAdmissionEnvelope
	if len(raw) > 65536 || strictDecode(raw, &envelope) != nil {
		return "", errors.New("invalid Cell admission encoding")
	}
	a := envelope.Admission
	if a.Schema != "kerosene.cell-admission/v1" || a.NetworkID != policy.NetworkID || a.Epoch != policy.Epoch ||
		!nameRE.MatchString(a.CellID) || !nameRE.MatchString(a.ChangeID) || !nameRE.MatchString(a.OperatorID) ||
		!clusterUIDRE.MatchString(a.ClusterUID) || !hashRE.MatchString(a.ApprovalDigest) || !admissionNonceRE.MatchString(a.Nonce) ||
		a.CellID != expected.CellID || a.ClusterUID != expected.ClusterUID || a.OperatorID != expected.OperatorID ||
		a.ChangeID != expected.ChangeID || a.ApprovalDigest != expected.ApprovalDigest ||
		a.IssuedAt == 0 || a.ExpiresAt > maxExactJSONInteger || a.ExpiresAt <= a.IssuedAt ||
		a.ExpiresAt-a.IssuedAt > 3600 || now < a.IssuedAt || now >= a.ExpiresAt {
		return "", errors.New("Cell admission binding or validity mismatch")
	}
	if len(envelope.Signatures) < policy.Threshold || len(envelope.Signatures) > len(policy.Members) {
		return "", errors.New("Cell admission quorum unavailable")
	}
	message, err := json.Marshal(a)
	if err != nil {
		return "", errors.New("invalid Cell admission payload")
	}
	seen := map[string]bool{}
	for _, signature := range envelope.Signatures {
		encodedKey, known := policy.Members[signature.MemberID]
		if !known || seen[signature.MemberID] {
			return "", errors.New("invalid Cell admission signer")
		}
		key, _ := base64.StdEncoding.DecodeString(encodedKey)
		sig, err := base64.StdEncoding.Strict().DecodeString(signature.Signature)
		if err != nil || len(sig) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(sig) != signature.Signature ||
			!ed25519.Verify(key, message, sig) {
			return "", errors.New("invalid Cell admission signature")
		}
		seen[signature.MemberID] = true
	}
	return hashBytes(message), nil
}

type AdmissionVerification struct {
	Schema            string           `json:"schema"`
	AdmissionDigest   string           `json:"admissionDigest"`
	Consensus         *ConsensusResult `json:"consensus"`
	NonceConsumed     bool             `json:"nonceConsumed"`
	InstallAuthorized bool             `json:"installAuthorized"`
}

// Read-only composition: the approval digest cannot be selected by the caller.
func verifyOrderedCellAdmission(anchor TrustAnchor, proof ConsensusProof, releaseDigest string, sequence uint64,
	raw []byte, binding AdmissionBinding, now time.Time) (*AdmissionVerification, error) {
	consensus, err := verifyConsensus(anchor, proof, releaseDigest, sequence, now)
	if err != nil {
		return nil, err
	}
	if now.Unix() < 0 {
		return nil, errors.New("invalid admission clock")
	}
	binding.ApprovalDigest = consensus.ApprovalDigest
	digest, err := verifyCellAdmission(anchor.Policy, raw, binding, uint64(now.Unix()))
	if err != nil {
		return nil, err
	}
	return &AdmissionVerification{Schema: "kerosene.cell-admission-verification/v1", AdmissionDigest: digest, Consensus: consensus}, nil
}
