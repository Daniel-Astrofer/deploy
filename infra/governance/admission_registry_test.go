package main

import (
	"context"
	"testing"
	"time"
)

func TestAdmissionConsumptionRejectsUnverifiedEvidenceBeforeRegistry(t *testing.T) {
	policy, _ := testPolicy(t)
	result, err := consumeOrderedCellAdmission(context.Background(), nil, TrustAnchor{Policy: policy}, ConsensusProof{},
		"untrusted-release", 1, []byte(`{}`), AdmissionBinding{}, time.Now())
	if err == nil || result != nil {
		t.Fatal("unverified admission reached consumption")
	}
	if err.Error() == "authenticated admission registry unavailable" {
		t.Fatal("registry consulted before consensus verification")
	}
	if err := insertVerifiedAdmission(context.Background(), nil, CellAdmission{}, "untrusted-digest"); err == nil {
		t.Fatal("missing authenticated registry accepted")
	}
}
