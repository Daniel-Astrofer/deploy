package main

import "errors"

type AdmissionOperatorPolicy struct {
	Schema    string                            `json:"schema"`
	Operators map[string]AdmissionOperatorGrant `json:"operators"`
}

// expectedDigest must be independently provisioned with the Bank service,
// never accepted from request evidence or calculated to trust an arbitrary file.
func loadAdmissionOperatorPolicy(path, expectedDigest string) (*AdmissionOperatorPolicy, error) {
	if !hashRE.MatchString(expectedDigest) {
		return nil, errors.New("trusted operator policy digest required")
	}
	raw, err := registryCredentialFile(path, false)
	if err != nil {
		return nil, errors.New("protected operator policy unavailable")
	}
	if hashBytes(raw) != expectedDigest {
		return nil, errors.New("operator policy digest mismatch")
	}
	var policy AdmissionOperatorPolicy
	if strictDecode(raw, &policy) != nil || policy.Schema != "kerosene.bank-admission-operator-policy/v1" {
		return nil, errors.New("invalid operator policy encoding")
	}
	if _, err := admissionOperatorPolicyPins(policy.Operators); err != nil {
		return nil, err
	}
	return &policy, nil
}
