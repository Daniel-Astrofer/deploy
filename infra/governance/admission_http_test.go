package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

type admissionUnreadBody struct{}

func (admissionUnreadBody) Read([]byte) (int, error) { panic("unauthenticated body was read") }
func (admissionUnreadBody) Close() error             { return nil }

func TestAdmissionHTTPRejectsBeforeReadingUnauthenticatedBody(t *testing.T) {
	var audit bytes.Buffer
	handler := &admissionHTTPHandler{Operators: map[string]AdmissionOperatorGrant{"operator-example": {
		SPKIPins: []string{"sha256:" + hex.EncodeToString(make([]byte, 32))}, Cells: []string{"cell-example"}, Operations: []string{"consume"}}}, Audit: log.New(&audit, "", 0)}
	request := httptest.NewRequest(http.MethodPost, "https://bank.example/v1/cell/admissions/consume", nil)
	request.Header.Set("Content-Type", "application/json")
	request.Body = admissionUnreadBody{}
	request.Header.Set("X-Operator-Id", "operator-example")
	request.Header.Set("Authorization", "Bearer synthetic-must-not-be-logged")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusForbidden || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("unauthenticated request accepted")
	}
	if !strings.Contains(audit.String(), "operation=consume operator=unverified cell=unverified status=403") ||
		strings.Contains(audit.String(), "synthetic-must-not-be-logged") || strings.Contains(audit.String(), "operator-example") {
		t.Fatal("audit leaked caller attribution or token")
	}
}

// Used by the opt-in real PostgreSQL/consensus test. HTTP uses real mTLS.
func testAdmissionHTTPConsumption(t *testing.T, db *sql.DB, anchor TrustAnchor, proof ConsensusProof,
	releaseDigest string, raw []byte, binding AdmissionBinding) *AdmissionVerification {
	t.Helper()
	ca := registryCA(t)
	serverPEM, serverKey, serverCert := registryLeaf(t, ca, "localhost", false)
	_, _, clientCert := registryLeaf(t, ca, "synthetic-operator", true)
	leaf, err := x509.ParseCertificate(clientCert.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	spki := sha256.Sum256(leaf.RawSubjectPublicKeyInfo)
	pin := "sha256:" + hex.EncodeToString(spki[:])
	handler := &admissionHTTPHandler{DB: db, Anchor: anchor, Cells: map[string]string{binding.CellID: binding.ClusterUID},
		Operators: map[string]AdmissionOperatorGrant{binding.OperatorID: {SPKIPins: []string{pin}, Cells: []string{binding.CellID}, Operations: []string{"consume", "inspect-recovery"}}}}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(ca.pem)
	prepared, err := newAdmissionServer("127.0.0.1:0", handler,
		&tls.Config{Certificates: []tls.Certificate{serverCert}, ClientCAs: pool, ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS12}, log.New(io.Discard, "", 0))
	if err != nil {
		t.Fatal("secure admission server configuration refused", err)
	}
	if prepared.ReadHeaderTimeout <= 0 || prepared.ReadTimeout <= 0 || prepared.WriteTimeout <= 0 || prepared.MaxHeaderBytes != 16384 ||
		prepared.TLSConfig.ClientAuth != tls.RequireAndVerifyClientCert || !prepared.TLSConfig.SessionTicketsDisabled {
		t.Fatal("admission server limits missing")
	}
	// Caller-side policy mutations must not change the running server snapshot.
	delete(handler.Operators, binding.OperatorID)
	handler.Cells[binding.CellID] = "untrusted-replacement"
	server := httptest.NewUnstartedServer(prepared.Handler)
	server.Config = prepared
	server.TLS = prepared.TLSConfig
	server.StartTLS()
	defer server.Close()
	transport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, ServerName: "localhost", Certificates: []tls.Certificate{clientCert}, MinVersion: tls.VersionTLS12}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 5 * time.Second}
	var envelope CellAdmissionEnvelope
	if strictDecode(raw, &envelope) != nil {
		t.Fatal("invalid synthetic envelope")
	}
	request := admissionHTTPRequest{Schema: "kerosene.bank-cell-admission-request/v1", ReleaseDigest: releaseDigest, Sequence: 1, Proof: proof, Envelope: envelope}
	encodedRequest, _ := json.Marshal(request)
	unauthTransport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, ServerName: "localhost", MinVersion: tls.VersionTLS12}}
	defer unauthTransport.CloseIdleConnections()
	unauthClient := &http.Client{Transport: unauthTransport, Timeout: 5 * time.Second}
	if response, err := unauthClient.Post(server.URL+"/v1/cell/admissions/consume", "application/json", bytes.NewReader(encodedRequest)); err == nil {
		response.Body.Close()
		t.Fatal("missing client certificate reached HTTP")
	}
	duplicate := strings.Replace(string(encodedRequest), `"schema":`, `"schema":"duplicate","schema":`, 1)
	bad, err := client.Post(server.URL+"/v1/cell/admissions/consume", "application/json", strings.NewReader(duplicate))
	if err != nil {
		t.Fatal("malformed request transport failed")
	}
	bad.Body.Close()
	if bad.StatusCode != http.StatusBadRequest {
		t.Fatal("ambiguous admission JSON accepted")
	}
	call := func(route string, payload admissionHTTPRequest, expected int) *AdmissionVerification {
		t.Helper()
		body, _ := json.Marshal(payload)
		response, err := client.Post(server.URL+route, "application/json", bytes.NewReader(body))
		if err != nil {
			t.Fatal("authenticated admission HTTP request failed", err)
		}
		defer response.Body.Close()
		if response.StatusCode != expected || response.Header.Get("Cache-Control") != "no-store" {
			t.Fatal("unexpected admission HTTP status", response.StatusCode)
		}
		if expected != http.StatusOK {
			return nil
		}
		encoded, err := io.ReadAll(io.LimitReader(response.Body, 16385))
		if err != nil || len(encoded) > 16384 {
			t.Fatal("unbounded admission response")
		}
		var result AdmissionVerification
		if strictDecode(encoded, &result) != nil {
			t.Fatal("invalid admission response")
		}
		return &result
	}
	changed := request
	changed.Envelope.Admission.OperatorID = "other-operator"
	call("/v1/cell/admissions/consume", changed, http.StatusForbidden)
	result := call("/v1/cell/admissions/consume", request, http.StatusOK)
	if !result.NonceConsumed || result.InstallAuthorized {
		t.Fatal("consumption claimed install permission")
	}
	call("/v1/cell/admissions/consume", request, http.StatusConflict)
	recovered := call("/v1/cell/admissions/inspect-recovery", request, http.StatusOK)
	if recovered.AdmissionDigest != result.AdmissionDigest || recovered.InstallAuthorized {
		t.Fatal("HTTP recovery changed admission")
	}
	if os.Getenv("CELL_ADMISSION_REGISTRY_TLS_CA") != "" {
		// Exercise the service entry point against the same durable database,
		// using independently written synthetic authority and protected files.
		dir := t.TempDir()
		write := func(name string, raw []byte) string {
			path := filepath.Join(dir, name)
			if err := os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			return path
		}
		anchorJSON, _ := json.Marshal(anchor)
		policyJSON, _ := json.Marshal(AdmissionOperatorPolicy{Schema: "kerosene.bank-admission-operator-policy/v1", Operators: map[string]AdmissionOperatorGrant{binding.OperatorID: {SPKIPins: []string{pin}, Cells: []string{binding.CellID}, Operations: []string{"consume", "inspect-recovery"}}}})
		reservation, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		address := reservation.Addr().String()
		reservation.Close()
		port, _ := strconv.Atoi(os.Getenv("CELL_ADMISSION_REGISTRY_PORT"))
		config := AdmissionServiceConfig{Schema: "kerosene.bank-admission-service/v1", Listen: address,
			AnchorFile: write("anchor.json", anchorJSON), AnchorDigest: hashBytes(anchorJSON),
			OperatorPolicyFile: write("operators.json", policyJSON), OperatorPolicyDigest: hashBytes(policyJSON),
			ServerCertFile: write("server.crt", serverPEM), ServerKeyFile: write("server.key", serverKey), ClientCAFile: write("ca.crt", ca.pem),
			Cells: map[string]string{binding.CellID: binding.ClusterUID},
			Registry: RegistryConnectionConfig{Host: "localhost", Port: port, Database: os.Getenv("CELL_ADMISSION_REGISTRY_DATABASE"), User: os.Getenv("CELL_ADMISSION_REGISTRY_USER"),
				CAFile: os.Getenv("CELL_ADMISSION_REGISTRY_TLS_CA"), ClientCertFile: os.Getenv("CELL_ADMISSION_REGISTRY_TLS_CERT"), ClientKeyFile: os.Getenv("CELL_ADMISSION_REGISTRY_TLS_KEY"), PasswordFile: os.Getenv("CELL_ADMISSION_REGISTRY_PASSWORD_FILE")}}
		profile, _ := json.Marshal(config)
		path := write("service.json", profile)
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() { done <- runAdmissionService(ctx, path, hashBytes(profile), log.New(io.Discard, "", 0)) }()
		defer cancel()
		deadline := time.Now().Add(10 * time.Second)
		for {
			response, err := client.Post("https://"+address+"/v1/cell/admissions/inspect-recovery", "application/json", bytes.NewReader(encodedRequest))
			if err == nil {
				var inspected AdmissionVerification
				decodeErr := json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&inspected)
				response.Body.Close()
				if response.StatusCode != http.StatusOK || decodeErr != nil || inspected.AdmissionDigest != result.AdmissionDigest || inspected.InstallAuthorized {
					t.Fatal("service recovery failed")
				}
				break
			}
			select {
			case err := <-done:
				t.Fatal("service stopped before recovery", err)
			default:
			}
			if time.Now().After(deadline) {
				t.Fatal("service startup timed out")
			}
			time.Sleep(20 * time.Millisecond)
		}
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Fatal("service shutdown failed", err)
			}
		case <-time.After(10 * time.Second):
			t.Fatal("service shutdown timed out")
		}
		testAdmissionCLIRecovery(t, dir, path, hashBytes(profile), address, client, encodedRequest, result.AdmissionDigest)
	}
	return result
}
