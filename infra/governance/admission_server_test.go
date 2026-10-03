package main

import (
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"io"
	"log"
	"testing"
)

func TestAdmissionServerRejectsWeakTLSBeforeAnchorValidation(t *testing.T) {
	ca := registryCA(t)
	_, _, cert := registryLeaf(t, ca, "localhost", false)
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(ca.pem)
	base := &tls.Config{Certificates: []tls.Certificate{cert}, ClientCAs: pool, ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS12}
	handler := &admissionHTTPHandler{DB: &sql.DB{}}
	logger := log.New(io.Discard, "", 0)
	for _, change := range []func(*tls.Config){
		func(c *tls.Config) { c.ClientAuth = tls.RequireAnyClientCert }, func(c *tls.Config) { c.ClientAuth = tls.NoClientCert },
		func(c *tls.Config) { c.ClientCAs = nil }, func(c *tls.Config) { c.Certificates = nil },
		func(c *tls.Config) { c.MinVersion = tls.VersionTLS11 }, func(c *tls.Config) { c.MaxVersion = tls.VersionTLS11 },
		func(c *tls.Config) {
			c.GetConfigForClient = func(*tls.ClientHelloInfo) (*tls.Config, error) { return nil, nil }
		},
	} {
		altered := base.Clone()
		change(altered)
		server, err := newAdmissionServer("127.0.0.1:0", handler, altered, logger)
		if err == nil || server != nil || err.Error() != "admission server requires fixed mandatory mutual TLS" {
			t.Fatal("weak TLS did not fail at transport validation", err)
		}
	}
}
