package main

import (
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/lib/pq"
)

type RegistryConnectionConfig struct {
	Host           string `json:"host"`
	Database       string `json:"database"`
	User           string `json:"user"`
	Port           int    `json:"port"`
	CAFile         string `json:"caFile"`
	ClientCertFile string `json:"clientCertFile"`
	ClientKeyFile  string `json:"clientKeyFile"`
	PasswordFile   string `json:"passwordFile"`
}

func registryCredentialFile(path string, private bool) ([]byte, error) {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return nil, errors.New("invalid registry credential reference")
	}
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, errors.New("registry credential unavailable")
	}
	f := os.NewFile(uintptr(fd), "registry-credential")
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || (private && info.Mode().Perm()&0077 != 0) {
		return nil, errors.New("registry credential permissions invalid")
	}
	raw, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil || len(raw) == 0 || len(raw) > 65536 {
		return nil, errors.New("registry credential size invalid")
	}
	return raw, nil
}

// Protected references only; no arbitrary DSN, TLS fallback, home-directory
// certificate default, or inherited PG* connection overrides.
func openAdmissionRegistry(config RegistryConnectionConfig) (*sql.DB, error) {
	for _, item := range os.Environ() {
		if strings.HasPrefix(strings.SplitN(item, "=", 2)[0], "PG") {
			return nil, errors.New("registry connection forbids inherited PG environment")
		}
	}
	hostRE := regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
	if len(config.Host) > 253 || !hostRE.MatchString(config.Host) || net.ParseIP(config.Host) != nil ||
		config.Port < 1 || config.Port > 65535 || !regexp.MustCompile(`^[a-z][a-z0-9_]{0,62}$`).MatchString(config.Database) ||
		!regexp.MustCompile(`^[a-z][a-z0-9_]{0,62}$`).MatchString(config.User) {
		return nil, errors.New("invalid registry connection binding")
	}
	ca, err := registryCredentialFile(config.CAFile, false)
	if err != nil {
		return nil, err
	}
	cert, err := registryCredentialFile(config.ClientCertFile, false)
	if err != nil {
		return nil, err
	}
	key, err := registryCredentialFile(config.ClientKeyFile, true)
	if err != nil {
		return nil, err
	}
	password, err := registryCredentialFile(config.PasswordFile, true)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, errors.New("invalid registry TLS trust")
	}
	if _, err := tls.X509KeyPair(cert, key); err != nil {
		return nil, errors.New("invalid registry TLS identity")
	}
	if len(password) > 4096 || strings.ContainsAny(string(password), "\x00\r\n") {
		return nil, errors.New("invalid registry password reference")
	}
	quote := func(v string) string { return "'" + strings.NewReplacer("\\", "\\\\", "'", "\\'").Replace(v) + "'" }
	dsn := fmt.Sprintf("host=%s port=%d dbname=%s user=%s password=%s sslmode=verify-full sslinline=true sslrootcert=%s sslcert=%s sslkey=%s sslsni=1 connect_timeout=5 options='-c statement_timeout=10000 -c lock_timeout=5000' application_name=kerosene-bank-admission",
		quote(config.Host), config.Port, quote(config.Database), quote(config.User), quote(string(password)), quote(string(ca)), quote(string(cert)), quote(string(key)))
	connector, err := pq.NewConnector(dsn)
	if err != nil {
		return nil, errors.New("registry connector configuration failed")
	}
	db := sql.OpenDB(connector)
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)
	return db, nil
}
