package main

import (
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"testing"
	"time"
)

func TestAdmissionOperatorRequiresVerifiedPinnedUniqueIdentity(t *testing.T) {
	ca := registryCA(t)
	encoded, _, _ := registryLeaf(t, ca, "synthetic-operator", true)
	block, _ := pem.Decode(encoded)
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(cert.RawSubjectPublicKeyInfo)
	pin := "sha256:" + hex.EncodeToString(hash[:])
	state := tls.ConnectionState{HandshakeComplete: true, PeerCertificates: []*x509.Certificate{cert}, VerifiedChains: [][]*x509.Certificate{{cert, ca.cert}}}
	pins := map[string][]string{"operator-example": {pin}}
	policy := map[string]AdmissionOperatorGrant{"operator-example": {SPKIPins: []string{pin}, Cells: []string{"cell-example"}, Operations: []string{"consume"}}}
	if identity, err := authorizeAdmissionOperator(&state, policy, "cell-example", "consume", time.Now()); err != nil || identity != "operator-example" {
		t.Fatal("authorized administrator rejected", err)
	}
	for _, scope := range [][2]string{{"other-cell", "consume"}, {"cell-example", "inspect-recovery"}, {"cell-example", "update"}} {
		if _, err := authorizeAdmissionOperator(&state, policy, scope[0], scope[1], time.Now()); err == nil {
			t.Fatal("ungranted administrative scope accepted")
		}
	}
	for _, grant := range []AdmissionOperatorGrant{
		{SPKIPins: []string{pin}, Cells: []string{"*"}, Operations: []string{"consume"}},
		{SPKIPins: []string{pin}, Cells: []string{"cell-example", "cell-example"}, Operations: []string{"consume"}},
		{SPKIPins: []string{pin}, Cells: []string{"cell-example"}, Operations: []string{"consume", "consume"}},
		{SPKIPins: []string{pin}, Cells: []string{"cell-example"}, Operations: []string{"automatic-resume"}},
	} {
		if _, err := authorizeAdmissionOperator(&state, map[string]AdmissionOperatorGrant{"operator-example": grant}, "cell-example", "consume", time.Now()); err == nil {
			t.Fatal("invalid administrative policy accepted")
		}
	}
	if identity, err := admissionOperatorIdentity(&state, pins, time.Now()); err != nil || identity != "operator-example" {
		t.Fatal("pinned operator rejected", err)
	}
	for _, invalid := range []*tls.ConnectionState{nil, {}, {HandshakeComplete: true, PeerCertificates: []*x509.Certificate{nil}, VerifiedChains: state.VerifiedChains},
		{HandshakeComplete: true, PeerCertificates: state.PeerCertificates, VerifiedChains: [][]*x509.Certificate{{nil}}},
		{HandshakeComplete: true, PeerCertificates: state.PeerCertificates},
		{PeerCertificates: state.PeerCertificates, VerifiedChains: state.VerifiedChains}} {
		if _, err := admissionOperatorIdentity(invalid, pins, time.Now()); err == nil {
			t.Fatal("unverified transport accepted")
		}
	}
	for _, invalid := range []map[string][]string{{}, {"operator-example": {}}, {"operator-example": {pin, pin}},
		{"operator-example": {pin}, "other-operator": {pin}}, {"operator-example": {"sha256:" + hex.EncodeToString(make([]byte, 32))}}} {
		if _, err := admissionOperatorIdentity(&state, invalid, time.Now()); err == nil {
			t.Fatal("untrusted or ambiguous operator identity accepted")
		}
	}
	if _, err := admissionOperatorIdentity(&state, pins, cert.NotAfter); err == nil {
		t.Fatal("expired client accepted")
	}
	if _, err := admissionOperatorIdentity(&state, pins, cert.NotBefore.Add(-time.Second)); err == nil {
		t.Fatal("future client accepted")
	}
}
