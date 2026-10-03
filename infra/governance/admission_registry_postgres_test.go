package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	_ "github.com/lib/pq"
)

func admissionLabDSN(t *testing.T) string {
	t.Helper()
	if os.Getenv("CELL_ADMISSION_REGISTRY_DISPOSABLE") != "true" {
		t.Skip("explicit disposable registry lab required")
	}
	port := os.Getenv("CELL_ADMISSION_REGISTRY_PORT")
	role := os.Getenv("CELL_ADMISSION_REGISTRY_USER")
	database := os.Getenv("CELL_ADMISSION_REGISTRY_DATABASE")
	if !regexp.MustCompile(`^[0-9]{1,5}$`).MatchString(port) ||
		!regexp.MustCompile(`^[a-z][a-z0-9_]{0,62}$`).MatchString(role) ||
		!regexp.MustCompile(`^cell_admission_lab_[0-9a-f]{16}$`).MatchString(database) {
		t.Fatal("invalid isolated registry lab binding")
	}
	password := os.Getenv("CELL_ADMISSION_REGISTRY_PASSWORD")
	if password != "synthetic-registry-test-only" {
		t.Fatal("synthetic lab credential required")
	}
	// Plaintext is ONLY allowed for this explicitly isolated loopback test.
	dsn := fmt.Sprintf("host=127.0.0.1 port=%s user=%s password=%s dbname=%s sslmode=disable connect_timeout=5", port, role, password, database)
	return dsn
}

func TestAdmissionRegistryPostgres(t *testing.T) {
	db := admissionLabDB(t)
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if db.PingContext(ctx) != nil {
		t.Fatal("isolated registry unavailable")
	}
	bytes := make([]byte, 16)
	if _, err := rand.Read(bytes); err != nil {
		t.Fatal(err)
	}
	suffix := hex.EncodeToString(bytes)
	a := CellAdmission{Schema: "kerosene.cell-admission/v1", NetworkID: "bank-go-" + suffix, Epoch: 1, CellID: "cell-go-" + suffix,
		ClusterUID: fmt.Sprintf("%s-%s-%s-%s-%s", suffix[:8], suffix[8:12], suffix[12:16], suffix[16:20], suffix[20:]),
		OperatorID: "operator-example", ChangeID: "change-example", ApprovalDigest: "sha256:" + strings.Repeat("a", 64),
		Nonce: suffix + suffix, IssuedAt: uint64(time.Now().Unix()) - 1, ExpiresAt: uint64(time.Now().Unix()) + 60}
	message, _ := json.Marshal(a)
	digest := hashBytes(message)
	results := make(chan error, 2)
	var workers sync.WaitGroup
	for i := 0; i < 2; i++ {
		workers.Add(1)
		go func() { defer workers.Done(); results <- insertVerifiedAdmission(ctx, db, a, digest) }()
	}
	workers.Wait()
	close(results)
	successes := 0
	for err := range results {
		if err == nil {
			successes++
		} else if !strings.Contains(err.Error(), "explicit recovery required") {
			t.Fatal(err)
		}
	}
	if successes != 1 {
		var diagnostic string
		err := db.QueryRowContext(ctx, registryInsertSQL, a.NetworkID, a.Epoch, a.Nonce, a.CellID, a.ClusterUID, a.OperatorID, a.ChangeID, a.ApprovalDigest, digest, a.IssuedAt, a.ExpiresAt).Scan(&diagnostic)
		t.Fatal("synthetic concurrent consumption did not have one winner", err)
	}
	// Reconnect proves persistence across process connections, not server failover.
	other := admissionLabDB(t)
	defer other.Close()
	if insertVerifiedAdmission(ctx, other, a, digest) == nil {
		t.Fatal("replay consumed again")
	}
	var count int
	if err := other.QueryRowContext(ctx, "SELECT count(*) FROM cell_admission.consumed WHERE admission_digest=$1", digest).Scan(&count); err != nil || count != 1 {
		t.Fatal("committed record unavailable")
	}
	a.CellID += "-expired"
	a.NetworkID += "-expired"
	a.Nonce = strings.Repeat("e", 64)
	a.ExpiresAt = uint64(time.Now().Unix()) - 1
	a.IssuedAt = a.ExpiresAt - 30
	message, _ = json.Marshal(a)
	if insertVerifiedAdmission(ctx, db, a, hashBytes(message)) == nil {
		t.Fatal("database clock admitted expired intent")
	}
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM cell_admission.consumed WHERE network_id=$1", a.NetworkID).Scan(&count); err != nil || count != 0 {
		t.Fatal("expired intent recorded")
	}
}

func testOrderedAdmissionConsumptionPostgres(t *testing.T, anchor TrustAnchor, proof ConsensusProof,
	releaseDigest string, raw []byte, binding AdmissionBinding, now time.Time) {
	t.Helper()
	if os.Getenv("CELL_ADMISSION_REGISTRY_DISPOSABLE") != "true" {
		return
	}
	db := admissionLabDB(t)
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	count := func() int {
		var n int
		if err := db.QueryRowContext(ctx, "SELECT count(*) FROM cell_admission.consumed WHERE network_id=$1 AND cell_id=$2", anchor.Policy.NetworkID, binding.CellID).Scan(&n); err != nil {
			t.Fatal("registry read failed")
		}
		return n
	}
	if count() != 0 {
		t.Fatal("ordered admission requires fresh synthetic binding")
	}
	tampered := proof
	tampered.State = json.RawMessage(`{}`)
	if result, err := consumeOrderedCellAdmission(ctx, db, anchor, tampered, releaseDigest, 1, raw, binding, now); err == nil || result != nil {
		t.Fatal("invalid committed state admitted consumption")
	}
	if count() != 0 {
		t.Fatal("failed consensus verification wrote registry state")
	}
	result, err := consumeOrderedCellAdmission(ctx, db, anchor, proof, releaseDigest, 1, raw, binding, now)
	if err != nil || result == nil || !result.NonceConsumed || result.InstallAuthorized {
		t.Fatal("ordered admission consumption failed", err)
	}
	if count() != 1 {
		t.Fatal("successful consumption not durably recorded")
	}
	if result, err := consumeOrderedCellAdmission(ctx, db, anchor, proof, releaseDigest, 1, raw, binding, now); err == nil || result != nil {
		t.Fatal("consumed ordered admission silently resumed")
	}
	if count() != 1 {
		t.Fatal("replay altered registry")
	}
	recovered, err := inspectConsumedCellAdmission(ctx, db, anchor, proof, releaseDigest, 1, raw, binding, now)
	if err != nil || recovered == nil || !recovered.NonceConsumed || recovered.InstallAuthorized {
		t.Fatal("exact recovery inspection failed", err)
	}
	late, err := inspectConsumedCellAdmission(ctx, db, anchor, proof, releaseDigest, 1, raw, binding, now.Add(2*time.Minute))
	if err != nil || late == nil || late.InstallAuthorized {
		t.Fatal("expired initial window erased valid consumed history", err)
	}
	wrong := binding
	wrong.OperatorID = "different-operator"
	if result, err := inspectConsumedCellAdmission(ctx, db, anchor, proof, releaseDigest, 1, raw, wrong, now); err == nil || result != nil {
		t.Fatal("changed operator accepted for recovery")
	}
	if result, err := inspectConsumedCellAdmission(ctx, db, anchor, tampered, releaseDigest, 1, raw, binding, now); err == nil || result != nil {
		t.Fatal("invalid consensus accepted for recovery")
	}
	if count() != 1 {
		t.Fatal("read-only recovery inspection altered registry")
	}
}

func admissionLabDB(t *testing.T) *sql.DB {
	t.Helper()
	dsn := admissionLabDSN(t)
	var db *sql.DB
	var err error
	if ca := os.Getenv("CELL_ADMISSION_REGISTRY_TLS_CA"); ca != "" {
		port, _ := strconv.Atoi(os.Getenv("CELL_ADMISSION_REGISTRY_PORT"))
		db, err = openAdmissionRegistry(RegistryConnectionConfig{Host: "localhost", Port: port,
			Database: os.Getenv("CELL_ADMISSION_REGISTRY_DATABASE"), User: os.Getenv("CELL_ADMISSION_REGISTRY_USER"),
			CAFile: ca, ClientCertFile: os.Getenv("CELL_ADMISSION_REGISTRY_TLS_CERT"), ClientKeyFile: os.Getenv("CELL_ADMISSION_REGISTRY_TLS_KEY"),
			PasswordFile: os.Getenv("CELL_ADMISSION_REGISTRY_PASSWORD_FILE")})
		if err != nil {
			t.Fatal("secure registry connector unavailable", err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		var authenticatedTLS bool
		if err := db.QueryRowContext(ctx, "SELECT ssl AND client_dn IS NOT NULL FROM pg_stat_ssl WHERE pid=pg_backend_pid()").Scan(&authenticatedTLS); err != nil || !authenticatedTLS {
			db.Close()
			t.Fatal("PostgreSQL connection lacks authenticated TLS", err)
		}
	} else {
		db, err = sql.Open("postgres", dsn)
	}
	if err != nil {
		t.Fatal("isolated registry unavailable")
	}
	return db
}
