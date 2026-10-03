package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type admissionUnreadBody struct{}

func (admissionUnreadBody) Read([]byte) (int, error) { panic("unauthenticated body was read") }
func (admissionUnreadBody) Close() error             { return nil }

func TestAdmissionHTTPRejectsBeforeReadingUnauthenticatedBody(t *testing.T) {
	handler := &admissionHTTPHandler{Operators: map[string]AdmissionOperatorGrant{"operator-example": {
		SPKIPins: []string{"sha256:" + hex.EncodeToString(make([]byte, 32))}, Cells: []string{"cell-example"}, Operations: []string{"consume"}}}}
	request := httptest.NewRequest(http.MethodPost, "https://bank.example/v1/cell/admissions/consume", nil)
	request.Header.Set("Content-Type", "application/json")
	request.Body = admissionUnreadBody{}
	request.Header.Set("X-Operator-Id", "operator-example")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusForbidden || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("unauthenticated request accepted")
	}
}

// Used by the opt-in real PostgreSQL/consensus test. HTTP uses real mTLS.
func testAdmissionHTTPConsumption(t *testing.T, db *sql.DB, anchor TrustAnchor, proof ConsensusProof,
	releaseDigest string, raw []byte, binding AdmissionBinding) *AdmissionVerification {
	t.Helper()
	ca := registryCA(t)
	_, _, serverCert := registryLeaf(t, ca, "localhost", false)
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
	return result
}
