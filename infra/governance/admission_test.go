package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestCellAdmissionContractsInteroperability(t *testing.T) {
	path := os.Getenv("CELL_ADMISSION_CONTRACT_VECTOR")
	if path == "" {
		t.Skip("explicit Contracts-owned vector path required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var vector struct {
		FixtureOnly bool                  `json:"fixtureOnly"`
		Policy      Policy                `json:"policy"`
		Envelope    CellAdmissionEnvelope `json:"envelope"`
		Canonical   string                `json:"canonicalPayloadUtf8"`
		Digest      string                `json:"admissionDigest"`
		Now         uint64                `json:"nowUnixSeconds"`
	}
	if err := strictDecode(raw, &vector); err != nil {
		t.Fatal(err)
	}
	if !vector.FixtureOnly {
		t.Fatal("not a synthetic interoperability fixture")
	}
	a := vector.Envelope.Admission
	canonical, err := json.Marshal(a)
	if err != nil || string(canonical) != vector.Canonical {
		t.Fatal("Rust/Go canonical bytes mismatch", err)
	}
	envelope, _ := json.Marshal(vector.Envelope)
	digest, err := verifyCellAdmission(vector.Policy, envelope, AdmissionBinding{a.CellID, a.ClusterUID, a.OperatorID, a.ChangeID, a.ApprovalDigest}, vector.Now)
	if err != nil || digest != vector.Digest {
		t.Fatal("Rust signatures/digest rejected by Go", err)
	}
}

func TestCellAdmissionPinnedQuorumAndBindings(t *testing.T) {
	policy, keys := testPolicy(t)
	a := CellAdmission{Schema: "kerosene.cell-admission/v1", NetworkID: policy.NetworkID, Epoch: 1,
		CellID: "cell-example", ClusterUID: "80cf8d2f-172d-4d43-ba99-1734b32184b1",
		OperatorID: "operator-example", ChangeID: "change-example", ApprovalDigest: "sha256:" + strings.Repeat("a", 64),
		Nonce: strings.Repeat("b", 64), IssuedAt: 1000, ExpiresAt: 1100}
	binding := AdmissionBinding{a.CellID, a.ClusterUID, a.OperatorID, a.ChangeID, a.ApprovalDigest}
	sign := func(payload CellAdmission) CellAdmissionEnvelope {
		message, _ := json.Marshal(payload)
		e := CellAdmissionEnvelope{Admission: payload}
		for _, member := range []string{"validator-1", "validator-2", "validator-3"} {
			e.Signatures = append(e.Signatures, ProposalSignature{member, base64.StdEncoding.EncodeToString(ed25519.Sign(keys[member], message))})
		}
		return e
	}
	check := func(e CellAdmissionEnvelope, b AdmissionBinding, now uint64, success bool) {
		t.Helper()
		raw, _ := json.Marshal(e)
		digest, err := verifyCellAdmission(policy, raw, b, now)
		if (err == nil) != success {
			t.Fatalf("unexpected admission result: %v", err)
		}
		if success {
			message, _ := json.Marshal(e.Admission)
			if digest != hashBytes(message) {
				t.Fatal("wrong payload digest")
			}
		}
	}
	check(sign(a), binding, 1000, true)
	check(sign(a), binding, 1099, true)
	check(sign(a), binding, 999, false)
	check(sign(a), binding, 1100, false)
	e := sign(a)
	e.Signatures = e.Signatures[:2]
	check(e, binding, 1000, false)
	e = sign(a)
	e.Signatures[2] = e.Signatures[0]
	check(e, binding, 1000, false)
	e = sign(a)
	e.Signatures[0].MemberID = "foreign-member"
	check(e, binding, 1000, false)
	e = sign(a)
	e.Signatures[0].Signature = base64.StdEncoding.EncodeToString(make([]byte, 64))
	check(e, binding, 1000, false)
	e = sign(a)
	e.Admission.Nonce = strings.Repeat("c", 64)
	check(e, binding, 1000, false)
	for _, change := range []func(*AdmissionBinding){
		func(b *AdmissionBinding) { b.CellID = "other-cell" }, func(b *AdmissionBinding) { b.ClusterUID = "another-cluster" },
		func(b *AdmissionBinding) { b.OperatorID = "other-operator" }, func(b *AdmissionBinding) { b.ChangeID = "other-change" },
		func(b *AdmissionBinding) { b.ApprovalDigest = "sha256:" + strings.Repeat("c", 64) },
	} {
		altered := binding
		change(&altered)
		check(sign(a), altered, 1000, false)
	}
	for _, change := range []func(*CellAdmission){
		func(p *CellAdmission) { p.NetworkID = "foreign-bank" }, func(p *CellAdmission) { p.Epoch = 2 },
		func(p *CellAdmission) { p.ExpiresAt = 4601 }, func(p *CellAdmission) { p.IssuedAt = 0 },
		func(p *CellAdmission) { p.ExpiresAt = maxExactJSONInteger + 1 },
	} {
		altered := a
		change(&altered)
		check(sign(altered), binding, 1000, false)
	}
	raw, _ := json.Marshal(sign(a))
	for _, invalid := range [][]byte{append(raw, raw...), []byte(strings.Replace(string(raw), `"epoch":1`, `"epoch":1,"epoch":1`, 1)),
		[]byte(strings.Replace(string(raw), `"signatures":`, `"publicKey":"untrusted","signatures":`, 1)), make([]byte, 65537)} {
		if _, err := verifyCellAdmission(policy, invalid, binding, 1000); err == nil {
			t.Fatal("ambiguous/oversized envelope accepted")
		}
	}
}
