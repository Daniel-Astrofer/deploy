package main

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
)

// References are provisioned independently by Bank operators, never by an
// admission request. Parent directories remain part of the trusted boundary.
type AdmissionServiceConfig struct {
	Schema               string                   `json:"schema"`
	Listen               string                   `json:"listen"`
	AnchorFile           string                   `json:"anchorFile"`
	AnchorDigest         string                   `json:"anchorDigest"`
	OperatorPolicyFile   string                   `json:"operatorPolicyFile"`
	OperatorPolicyDigest string                   `json:"operatorPolicyDigest"`
	ServerCertFile       string                   `json:"serverCertFile"`
	ServerKeyFile        string                   `json:"serverKeyFile"`
	ClientCAFile         string                   `json:"clientCaFile"`
	Cells                map[string]string        `json:"cells"`
	Registry             RegistryConnectionConfig `json:"registry"`
}

func loadAdmissionServiceConfig(path, digest string) (*AdmissionServiceConfig, error) {
	if !hashRE.MatchString(digest) {
		return nil, errors.New("independently provisioned admission configuration digest required")
	}
	raw, err := registryCredentialFile(path, false)
	if err != nil || hashBytes(raw) != digest {
		return nil, errors.New("admission configuration unavailable or digest mismatch")
	}
	var config AdmissionServiceConfig
	if strictDecode(raw, &config) != nil || config.Schema != "kerosene.bank-admission-service/v1" {
		return nil, errors.New("invalid admission service configuration")
	}
	return &config, nil
}

func admissionServiceAuthority(config AdmissionServiceConfig) (TrustAnchor, *AdmissionOperatorPolicy, *tls.Config, error) {
	var anchor TrustAnchor
	raw, err := registryCredentialFile(config.AnchorFile, false)
	if err != nil || !hashRE.MatchString(config.AnchorDigest) || hashBytes(raw) != config.AnchorDigest || strictDecode(raw, &anchor) != nil {
		return anchor, nil, nil, errors.New("protected admission anchor unavailable or invalid")
	}
	operators, err := loadAdmissionOperatorPolicy(config.OperatorPolicyFile, config.OperatorPolicyDigest)
	if err != nil {
		return anchor, nil, nil, err
	}
	cert, err := registryCredentialFile(config.ServerCertFile, false)
	if err != nil {
		return anchor, nil, nil, err
	}
	key, err := registryCredentialFile(config.ServerKeyFile, true)
	if err != nil {
		return anchor, nil, nil, err
	}
	identity, err := tls.X509KeyPair(cert, key)
	if err != nil {
		return anchor, nil, nil, errors.New("invalid admission server identity")
	}
	ca, err := registryCredentialFile(config.ClientCAFile, false)
	if err != nil {
		return anchor, nil, nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return anchor, nil, nil, errors.New("invalid admission client trust")
	}
	return anchor, operators, &tls.Config{MinVersion: tls.VersionTLS12, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: pool, Certificates: []tls.Certificate{identity}}, nil
}
