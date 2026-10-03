package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAdmissionOperatorPolicyIsProtectedPinnedAndStrict(t *testing.T) {
	policy := AdmissionOperatorPolicy{Schema: "kerosene.bank-admission-operator-policy/v1", Operators: map[string]AdmissionOperatorGrant{
		"operator-example": {SPKIPins: []string{"sha256:" + strings.Repeat("a", 64)}, Cells: []string{"cell-example"}, Operations: []string{"consume"}},
	}}
	raw, _ := json.Marshal(policy)
	path := filepath.Join(t.TempDir(), "policy.json")
	write := func(bytes []byte) {
		if err := os.WriteFile(path, bytes, 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(raw)
	loaded, err := loadAdmissionOperatorPolicy(path, hashBytes(raw))
	if err != nil || loaded == nil {
		t.Fatal("protected policy refused", err)
	}
	if result, err := loadAdmissionOperatorPolicy(path, "sha256:"+strings.Repeat("b", 64)); err == nil || result != nil {
		t.Fatal("changed policy accepted")
	}
	for _, invalid := range [][]byte{[]byte(`{"schema":"kerosene.bank-admission-operator-policy/v1","operators":{}}`),
		append(raw, raw...), []byte(strings.Replace(string(raw), `"operators":`, `"operators":{},"operators":`, 1)),
		[]byte(strings.Replace(string(raw), `"operators":`, `"force":true,"operators":`, 1))} {
		write(invalid)
		if result, err := loadAdmissionOperatorPolicy(path, hashBytes(invalid)); err == nil || result != nil {
			t.Fatal("ambiguous/unsafe policy accepted")
		}
	}
	write(raw)
	if err := os.Chmod(path, 0666); err != nil {
		t.Fatal(err)
	}
	if result, err := loadAdmissionOperatorPolicy(path, hashBytes(raw)); err == nil || result != nil {
		t.Fatal("shared writable policy accepted")
	}
}
