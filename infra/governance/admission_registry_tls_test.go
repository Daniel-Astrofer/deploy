package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type registryTestCA struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
	pem  []byte
}

func registryCA(t *testing.T) registryTestCA {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "synthetic-registry-ca"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, cert, cert, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return registryTestCA{parsed, key, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})}
}
func registryLeaf(t *testing.T, ca registryTestCA, host string, client bool) ([]byte, []byte, tls.Certificate) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	usage := x509.ExtKeyUsageServerAuth
	if client {
		usage = x509.ExtKeyUsageClientAuth
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "synthetic-registry-leaf"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), DNSNames: []string{host},
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{usage}}
	der, err := x509.CreateCertificate(rand.Reader, cert, ca.cert, &key.PublicKey, ca.key)
	if err != nil {
		t.Fatal(err)
	}
	encodedKey, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encodedKey})
	pair, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	return certPEM, keyPEM, pair
}

// Minimal PostgreSQL wire peer ONLY to qualify TLS transport, not database SQL.
func TestAdmissionRegistryRealTLS(t *testing.T) {
	serverCA, clientCA, foreignCA := registryCA(t), registryCA(t), registryCA(t)
	for _, scenario := range []string{"valid", "wrong-server-ca", "wrong-hostname", "untrusted-client", "unpinned-client", "wrong-cell-scope", "wrong-operation", "tls-refused"} {
		t.Run(scenario, func(t *testing.T) {
			dir := t.TempDir()
			host := "localhost"
			if scenario == "wrong-hostname" {
				host = "foreign.example"
			}
			_, _, serverCert := registryLeaf(t, serverCA, host, false)
			clientIssuer := clientCA
			if scenario == "untrusted-client" {
				clientIssuer = foreignCA
			}
			clientCert, clientKey, _ := registryLeaf(t, clientIssuer, "synthetic-client", true)
			block, _ := pem.Decode(clientCert)
			leaf, err := x509.ParseCertificate(block.Bytes)
			if err != nil {
				t.Fatal(err)
			}
			spki := sha256.Sum256(leaf.RawSubjectPublicKeyInfo)
			pin := "sha256:" + hex.EncodeToString(spki[:])
			if scenario == "unpinned-client" {
				pin = "sha256:" + hex.EncodeToString(make([]byte, 32))
			}
			trust := serverCA.pem
			if scenario == "wrong-server-ca" {
				trust = foreignCA.pem
			}
			write := func(name string, data []byte) string {
				path := filepath.Join(dir, name)
				if err := os.WriteFile(path, data, 0600); err != nil {
					t.Fatal(err)
				}
				return path
			}
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			result := make(chan bool, 1)
			go func() {
				conn, err := listener.Accept()
				if err != nil {
					result <- false
					return
				}
				defer conn.Close()
				conn.SetDeadline(time.Now().Add(5 * time.Second))
				request := make([]byte, 8)
				if _, err := io.ReadFull(conn, request); err != nil || binary.BigEndian.Uint32(request[:4]) != 8 || binary.BigEndian.Uint32(request[4:]) != 80877103 {
					result <- false
					return
				}
				if scenario == "tls-refused" {
					conn.Write([]byte("N"))
					result <- false
					return
				}
				if _, err := conn.Write([]byte("S")); err != nil {
					result <- false
					return
				}
				pool := x509.NewCertPool()
				pool.AppendCertsFromPEM(clientCA.pem)
				secure := tls.Server(conn, &tls.Config{Certificates: []tls.Certificate{serverCert}, ClientCAs: pool, ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS12})
				if err := secure.Handshake(); err != nil {
					result <- false
					return
				}
				state := secure.ConnectionState()
				grant := AdmissionOperatorGrant{SPKIPins: []string{pin}, Cells: []string{"cell-example"}, Operations: []string{"consume"}}
				if scenario == "wrong-cell-scope" {
					grant.Cells = []string{"other-cell"}
				}
				if scenario == "wrong-operation" {
					grant.Operations = []string{"inspect-recovery"}
				}
				identity, err := authorizeAdmissionOperator(&state, map[string]AdmissionOperatorGrant{"operator-example": grant}, "cell-example", "consume", time.Now())
				if err != nil || identity != "operator-example" {
					result <- false
					return
				}
				length := make([]byte, 4)
				if _, err := io.ReadFull(secure, length); err != nil {
					result <- false
					return
				}
				size := binary.BigEndian.Uint32(length)
				if size < 4 || size > 4096 {
					result <- false
					return
				}
				if _, err := io.CopyN(io.Discard, secure, int64(size-4)); err != nil {
					result <- false
					return
				}
				// AuthenticationOk, ReadyForQuery; then EmptyQueryResponse for Ping.
				if _, err := secure.Write([]byte{'R', 0, 0, 0, 8, 0, 0, 0, 0, 'Z', 0, 0, 0, 5, 'I'}); err != nil {
					result <- false
					return
				}
				header := make([]byte, 5)
				if _, err := io.ReadFull(secure, header); err != nil || header[0] != 'Q' {
					result <- false
					return
				}
				size = binary.BigEndian.Uint32(header[1:])
				if size < 4 || size > 4096 {
					result <- false
					return
				}
				if _, err := io.CopyN(io.Discard, secure, int64(size-4)); err != nil {
					result <- false
					return
				}
				_, err = secure.Write([]byte{'I', 0, 0, 0, 4, 'Z', 0, 0, 0, 5, 'I'})
				result <- err == nil
			}()
			config := RegistryConnectionConfig{Host: "localhost", Port: listener.Addr().(*net.TCPAddr).Port, Database: "admission_registry", User: "admission_service",
				CAFile: write("ca.pem", trust), ClientCertFile: write("client.pem", clientCert), ClientKeyFile: write("client.key", clientKey), PasswordFile: write("password", []byte("synthetic-test-only"))}
			db, err := openAdmissionRegistry(config)
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
			defer cancel()
			err = db.PingContext(ctx)
			if (err == nil) != (scenario == "valid") {
				t.Fatal("unexpected registry TLS outcome", scenario)
			}
			select {
			case accepted := <-result:
				if accepted != (scenario == "valid") {
					t.Fatal("TLS peer accepted unexpected startup")
				}
			case <-time.After(6 * time.Second):
				t.Fatal("TLS peer did not terminate")
			}
		})
	}
}
