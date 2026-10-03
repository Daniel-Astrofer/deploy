package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestAdmissionServiceConfigProtectedDigest(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.json")
	raw := []byte(`{"schema":"kerosene.bank-admission-service/v1","listen":"127.0.0.1:8443","cells":{"cell-a":"00000000-0000-0000-0000-000000000001"},"registry":{"host":"localhost","port":5432,"database":"bank_registry","user":"bank_service"}}`)
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	config, err := loadAdmissionServiceConfig(path, hashBytes(raw))
	if err != nil || config.Registry.Database != "bank_registry" || config.Registry.Port != 5432 {
		t.Fatalf("configuration load: %v", err)
	}
	if _, err := loadAdmissionServiceConfig(path, hashBytes([]byte("other"))); err == nil {
		t.Fatal("accepted wrong digest")
	}
	if _, err := loadAdmissionServiceConfig(path, ""); err == nil {
		t.Fatal("accepted absent digest")
	}
	if err := os.Chmod(path, 0666); err != nil {
		t.Fatal(err)
	}
	if _, err := loadAdmissionServiceConfig(path, hashBytes(raw)); err == nil {
		t.Fatal("accepted shared-writable configuration")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	for _, invalid := range []string{
		`{"schema":"kerosene.bank-admission-service/v1","schema":"kerosene.bank-admission-service/v1"}`,
		`{"schema":"kerosene.bank-admission-service/v1","registry":{"password":"secret"}}`,
		`{"schema":"unknown"}`,
	} {
		if err := os.WriteFile(path, []byte(invalid), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := loadAdmissionServiceConfig(path, hashBytes([]byte(invalid))); err == nil {
			t.Fatal("accepted invalid configuration")
		}
	}
}

func TestAdmissionServiceAuthorityRejectsUnpinnedAnchor(t *testing.T) {
	path := filepath.Join(t.TempDir(), "anchor.json")
	if err := os.WriteFile(path, []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := admissionServiceAuthority(AdmissionServiceConfig{AnchorFile: path}); err == nil {
		t.Fatal("accepted unpinned anchor")
	}
}
