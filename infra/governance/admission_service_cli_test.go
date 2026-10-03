package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// Only invoked by the explicitly disposable real PostgreSQL/TLS lab.
func testAdmissionCLIRecovery(t *testing.T, dir, profile, digest, address string, client *http.Client, request []byte, admissionDigest string) {
	t.Helper()
	buildContext, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	binary := filepath.Join(dir, "bank-admission")
	build := exec.CommandContext(buildContext, "go", "build", "-mod=readonly", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build admission CLI: %v %s", err, output)
	}
	// A second independent process must inspect retained state after SIGTERM;
	// neither process may claim installation permission or consume again.
	for iteration := 0; iteration < 2; iteration++ {
		cmd := exec.Command(binary, "serve-admission", "--service-config", profile, "--service-config-digest", digest)
		cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		stopped := false
		cleanup := func() {
			if !stopped {
				_ = cmd.Process.Kill()
				<-done
				stopped = true
			}
		}
		func() {
			defer cleanup()
			deadline := time.Now().Add(10 * time.Second)
			for {
				response, err := client.Post("https://"+address+"/v1/cell/admissions/inspect-recovery", "application/json", bytes.NewReader(request))
				if err == nil {
					var result AdmissionVerification
					decodeErr := json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&result)
					response.Body.Close()
					if response.StatusCode != http.StatusOK || decodeErr != nil || result.AdmissionDigest != admissionDigest || !result.NonceConsumed || result.InstallAuthorized {
						t.Fatal("compiled service recovery failed")
					}
					break
				}
				select {
				case err := <-done:
					stopped = true
					t.Fatal("compiled service stopped before recovery", err)
				default:
				}
				if time.Now().After(deadline) {
					t.Fatal("compiled service startup timed out")
				}
				time.Sleep(20 * time.Millisecond)
			}
			response, err := client.Post("https://"+address+"/v1/cell/admissions/consume", "application/json", bytes.NewReader(request))
			if err != nil {
				t.Fatal("compiled replay transport failed", err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusConflict {
				t.Fatal("compiled service accepted consumed nonce")
			}
			if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
				t.Fatal(err)
			}
			select {
			case err := <-done:
				stopped = true
				if err != nil {
					t.Fatal("compiled service SIGTERM failed", err)
				}
			case <-time.After(10 * time.Second):
				t.Fatal("compiled service SIGTERM timed out")
			}
		}()
	}
}
