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
