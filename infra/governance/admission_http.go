package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"sync"
	"time"
)

// Backend request protocol owned alongside the CometBFT proof implementation.
// Public admission payload remains owned by Contracts, unchanged.
type admissionHTTPRequest struct {
	Schema        string                `json:"schema"`
	ReleaseDigest string                `json:"releaseLockCanonicalDigest"`
	Sequence      uint64                `json:"sequence"`
	Proof         ConsensusProof        `json:"proof"`
	Envelope      CellAdmissionEnvelope `json:"envelope"`
}

// All fields are independently provisioned server inputs, never request values.
// Cells bind previously authenticated/qualified live cluster identities. Their
// provisioning/revalidation and production listener setup remain separate work.
type admissionHTTPHandler struct {
	DB        *sql.DB
	Anchor    TrustAnchor
	Operators map[string]AdmissionOperatorGrant
	Cells     map[string]string
	mu        sync.Mutex
	active    int
}

func (h *admissionHTTPHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	reject := func(status int, code string) {
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]string{"code": code})
	}
	operation := ""
	switch r.URL.Path {
	case "/v1/cell/admissions/consume":
		operation = "consume"
	case "/v1/cell/admissions/inspect-recovery":
		operation = "inspect-recovery"
	default:
		reject(http.StatusNotFound, "route_unknown")
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		reject(http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery || r.Header.Get("Content-Type") != "application/json" || r.Header.Get("Content-Encoding") != "" {
		reject(http.StatusBadRequest, "request_invalid")
		return
	}
	pins, err := admissionOperatorPolicyPins(h.Operators)
	if err != nil {
		reject(http.StatusServiceUnavailable, "operator_policy_unavailable")
		return
	}
	now := time.Now()
	identity, err := admissionOperatorIdentity(r.TLS, pins, now)
	if err != nil {
		reject(http.StatusForbidden, "operator_certificate_required")
		return
	}
	h.mu.Lock()
	if h.active >= 4 {
		h.mu.Unlock()
		reject(http.StatusTooManyRequests, "admission_busy")
		return
	}
	h.active++
	h.mu.Unlock()
	defer func() { h.mu.Lock(); h.active--; h.mu.Unlock() }()
	// Authenticate before accepting an expensive consensus proof or reading body.
	r.Body = http.MaxBytesReader(w, r.Body, 8*1024*1024)
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		reject(http.StatusBadRequest, "request_invalid")
		return
	}
	var request admissionHTTPRequest
	if strictDecode(raw, &request) != nil || request.Schema != "kerosene.bank-cell-admission-request/v1" ||
		request.Sequence == 0 || request.Sequence > maxExactJSONInteger || !hashRE.MatchString(request.ReleaseDigest) {
		reject(http.StatusBadRequest, "request_invalid")
		return
	}
	cell := request.Envelope.Admission.CellID
	operator, err := authorizeAdmissionOperator(r.TLS, h.Operators, cell, operation, now)
	if err != nil || operator != identity || request.Envelope.Admission.OperatorID != identity {
		reject(http.StatusForbidden, "operator_scope_denied")
		return
	}
	cluster := h.Cells[cell]
	if !clusterUIDRE.MatchString(cluster) {
		reject(http.StatusServiceUnavailable, "cell_binding_unavailable")
		return
	}
	if h.DB == nil {
		reject(http.StatusServiceUnavailable, "registry_unavailable")
		return
	}
	envelope, err := json.Marshal(request.Envelope)
	if err != nil {
		reject(http.StatusBadRequest, "request_invalid")
		return
	}
	binding := AdmissionBinding{CellID: cell, ClusterUID: cluster, OperatorID: identity, ChangeID: request.Envelope.Admission.ChangeID}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	var result *AdmissionVerification
	if operation == "consume" {
		result, err = consumeOrderedCellAdmission(ctx, h.DB, h.Anchor, request.Proof, request.ReleaseDigest, request.Sequence, envelope, binding, now)
	} else {
		result, err = inspectConsumedCellAdmission(ctx, h.DB, h.Anchor, request.Proof, request.ReleaseDigest, request.Sequence, envelope, binding, now)
	}
	if err != nil {
		reject(http.StatusConflict, "admission_unverified_or_recovery_required")
		return
	}
	// Commit precedes response. A lost response is uncertain; clients must inspect
	// exact retained consumption, never assume the nonce is free or blindly retry.
	json.NewEncoder(w).Encode(result)
}
