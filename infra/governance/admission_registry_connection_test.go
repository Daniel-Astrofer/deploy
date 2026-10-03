package main

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestRegistryConnectionRejectsOverridesAndUnsafeTargets(t *testing.T) {
	base := RegistryConnectionConfig{Host: "registry.example", Port: 5432, Database: "admission_registry", User: "admission_service"}
	for _, change := range []func(*RegistryConnectionConfig){
		func(c *RegistryConnectionConfig) { c.Host = "127.0.0.1" }, func(c *RegistryConnectionConfig) { c.Host = "/tmp/socket" },
		func(c *RegistryConnectionConfig) { c.Host = "registry.example?sslmode=disable" }, func(c *RegistryConnectionConfig) { c.Port = 0 },
		func(c *RegistryConnectionConfig) { c.User = "user options=unsafe" }, func(c *RegistryConnectionConfig) { c.Database = "postgres/foreign" },
	} {
		altered := base
		change(&altered)
		db, err := openAdmissionRegistry(altered)
		if err == nil || db != nil {
			t.Fatal("unsafe registry binding accepted")
		}
	}
	t.Setenv("PGSSLMODE", "disable")
	if db, err := openAdmissionRegistry(base); err == nil || db != nil || !strings.Contains(err.Error(), "inherited PG") {
		t.Fatal("TLS environment override accepted")
	}
}

func TestRegistryCredentialFilesAreBoundedPrivateAndRegular(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "synthetic-secret")
	if err := os.WriteFile(path, []byte("synthetic-test-only"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := registryCredentialFile(path, true); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := registryCredentialFile(path, true); err == nil {
		t.Fatal("public private credential accepted")
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	fifo := filepath.Join(dir, "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	for _, target := range []string{link, fifo, dir, "relative-file"} {
		if _, err := registryCredentialFile(target, false); err == nil {
			t.Fatal("nonregular credential accepted")
		}
	}
	if err := os.WriteFile(path, make([]byte, 65537), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := registryCredentialFile(path, false); err == nil {
		t.Fatal("oversized credential accepted")
	}
}
