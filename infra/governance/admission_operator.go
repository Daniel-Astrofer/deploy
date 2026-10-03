package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"time"
)

// Transport identity only, not administrative role/session authorization.
// Pins must come from independently provisioned server policy, not the request.
// The state must be supplied directly by the actual mandatory-mTLS listener.
func admissionOperatorIdentity(state *tls.ConnectionState, pins map[string][]string, now time.Time) (string, error) {
	if state == nil || !state.HandshakeComplete || len(state.PeerCertificates) == 0 || len(state.VerifiedChains) == 0 ||
		len(state.VerifiedChains[0]) == 0 || state.PeerCertificates[0] == nil || state.VerifiedChains[0][0] == nil ||
		!bytes.Equal(state.PeerCertificates[0].Raw, state.VerifiedChains[0][0].Raw) {
		return "", errors.New("verified operator client certificate required")
	}
	leaf := state.PeerCertificates[0]
	if now.Before(leaf.NotBefore) || !now.Before(leaf.NotAfter) {
		return "", errors.New("operator client certificate expired or not yet valid")
	}
	if len(pins) == 0 || len(pins) > 64 {
		return "", errors.New("invalid operator identity policy")
	}
	seen := map[string]bool{}
	identities := map[string]string{}
	for operator, digests := range pins {
		if !nameRE.MatchString(operator) || len(digests) == 0 || len(digests) > 64 {
			return "", errors.New("invalid operator identity policy")
		}
		for _, digest := range digests {
			if !hashRE.MatchString(digest) || seen[digest] {
				return "", errors.New("ambiguous operator identity policy")
			}
			seen[digest] = true
			identities[digest] = operator
		}
	}
	hash := sha256.Sum256(leaf.RawSubjectPublicKeyInfo)
	identity := identities["sha256:"+hex.EncodeToString(hash[:])]
	if identity == "" {
		return "", errors.New("operator client identity is not pinned")
	}
	return identity, nil
}

// Independently provisioned Bank administrative policy. Never decoded from an
// admission/request; initial bootstrap must not depend on its absent Core.
type AdmissionOperatorGrant struct {
	SPKIPins   []string `json:"spkiPins"`
	Cells      []string `json:"cells"`
	Operations []string `json:"operations"`
}

func authorizeAdmissionOperator(state *tls.ConnectionState, policy map[string]AdmissionOperatorGrant,
	cellID, operation string, now time.Time) (string, error) {
	if !nameRE.MatchString(cellID) || (operation != "consume" && operation != "inspect-recovery") {
		return "", errors.New("invalid administrative admission scope")
	}
	pins, err := admissionOperatorPolicyPins(policy)
	if err != nil {
		return "", err
	}
	identity, err := admissionOperatorIdentity(state, pins, now)
	if err != nil {
		return "", err
	}
	grant := policy[identity]
	cellAllowed, operationAllowed := false, false
	for _, cell := range grant.Cells {
		if cell == cellID {
			cellAllowed = true
		}
	}
	for _, op := range grant.Operations {
		if op == operation {
			operationAllowed = true
		}
	}
	if !cellAllowed || !operationAllowed {
		return "", errors.New("operator is not authorized for this Cell admission operation")
	}
	return identity, nil
}

func admissionOperatorPolicyPins(policy map[string]AdmissionOperatorGrant) (map[string][]string, error) {
	if len(policy) == 0 || len(policy) > 64 {
		return nil, errors.New("invalid admission administrative policy")
	}
	pins := map[string][]string{}
	seenPins := map[string]bool{}
	for identity, grant := range policy {
		if !nameRE.MatchString(identity) || len(grant.SPKIPins) == 0 || len(grant.SPKIPins) > 64 {
			return nil, errors.New("invalid admission administrative policy")
		}
		for _, pin := range grant.SPKIPins {
			if !hashRE.MatchString(pin) || seenPins[pin] {
				return nil, errors.New("ambiguous admission administrative policy")
			}
			seenPins[pin] = true
		}
		if len(grant.Cells) == 0 || len(grant.Cells) > 64 || len(grant.Operations) == 0 || len(grant.Operations) > 2 {
			return nil, errors.New("invalid admission administrative policy")
		}
		seenCells := map[string]bool{}
		seenOperations := map[string]bool{}
		for _, cell := range grant.Cells {
			if !nameRE.MatchString(cell) || seenCells[cell] {
				return nil, errors.New("invalid admission administrative policy")
			}
			seenCells[cell] = true
		}
		for _, op := range grant.Operations {
			if (op != "consume" && op != "inspect-recovery") || seenOperations[op] {
				return nil, errors.New("invalid admission administrative policy")
			}
			seenOperations[op] = true
		}
		pins[identity] = grant.SPKIPins
	}
	return pins, nil
}
