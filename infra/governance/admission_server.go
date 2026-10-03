package main

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"log"
	"net"
	"net/http"
	"strconv"
	"time"
)

// Prepares a server only; never binds a listener or provisions authority.
// Server inputs must already be independently authenticated/provisioned.
func newAdmissionServer(address string, handler *admissionHTTPHandler, config *tls.Config, logger *log.Logger) (*http.Server, error) {
	host, port, err := net.SplitHostPort(address)
	number, numberErr := strconv.Atoi(port)
	if err != nil || host == "" || numberErr != nil || number < 0 || number > 65535 || handler == nil || handler.DB == nil || logger == nil {
		return nil, errors.New("invalid admission server configuration")
	}
	if config == nil || config.ClientAuth != tls.RequireAndVerifyClientCert || config.ClientCAs == nil ||
		len(config.ClientCAs.Subjects()) == 0 || len(config.Certificates) == 0 || config.GetConfigForClient != nil || config.GetCertificate != nil ||
		config.MinVersion < tls.VersionTLS12 || (config.MaxVersion != 0 && config.MaxVersion < config.MinVersion) {
		return nil, errors.New("admission server requires fixed mandatory mutual TLS")
	}
	if err := validatePolicy(handler.Anchor.Policy); err != nil {
		return nil, err
	}
	if handler.Anchor.TrustingPeriodSeconds <= 0 || handler.Anchor.TrustingPeriodSeconds > 14*24*3600 {
		return nil, errors.New("invalid admission consensus trust period")
	}
	if _, err := parseLight(handler.Anchor.LightBlock, handler.Anchor.Policy.NetworkID); err != nil {
		return nil, errors.New("invalid admission consensus anchor")
	}
	if _, err := admissionOperatorPolicyPins(handler.Operators); err != nil {
		return nil, err
	}
	if len(handler.Cells) == 0 || len(handler.Cells) > 64 {
		return nil, errors.New("invalid admission cluster bindings")
	}
	seen := map[string]bool{}
	for cell, uid := range handler.Cells {
		if !nameRE.MatchString(cell) || !clusterUIDRE.MatchString(uid) || seen[uid] {
			return nil, errors.New("invalid admission cluster bindings")
		}
		seen[uid] = true
	}
	// Snapshot maps/raw proof anchor so later caller mutations cannot change
	// authorization policy under active requests. The pool remains shared.
	encoded, err := json.Marshal(struct {
		Anchor    TrustAnchor
		Operators map[string]AdmissionOperatorGrant
		Cells     map[string]string
	}{handler.Anchor, handler.Operators, handler.Cells})
	if err != nil {
		return nil, errors.New("invalid admission policy snapshot")
	}
	var snapshot struct {
		Anchor    TrustAnchor
		Operators map[string]AdmissionOperatorGrant
		Cells     map[string]string
	}
	if strictDecode(encoded, &snapshot) != nil {
		return nil, errors.New("invalid admission policy snapshot")
	}
	frozen := &admissionHTTPHandler{DB: handler.DB, Anchor: snapshot.Anchor, Operators: snapshot.Operators, Cells: snapshot.Cells}
	secure := config.Clone()
	secure.ClientCAs = config.ClientCAs.Clone()
	secure.NameToCertificate = nil
	secure.NextProtos = []string{"http/1.1"}
	secure.SessionTicketsDisabled = true
	return &http.Server{Addr: address, Handler: frozen, TLSConfig: secure, ErrorLog: logger,
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 40 * time.Second, IdleTimeout: 30 * time.Second,
		MaxHeaderBytes: 16384, TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){}}, nil
}
