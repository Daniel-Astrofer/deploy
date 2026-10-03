package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

// The caller supplies an authenticated, independently bound registry connection
// outside the Cell rollback domain. No DSN/credentials are accepted from evidence.
// Returned failure may be an uncertain committed outcome: never release/retry
// the nonce automatically. Existing consumption requires explicit recovery.
func consumeOrderedCellAdmission(ctx context.Context, db *sql.DB, anchor TrustAnchor, proof ConsensusProof,
	releaseDigest string, sequence uint64, raw []byte, binding AdmissionBinding, now time.Time) (*AdmissionVerification, error) {
	verified, err := verifyOrderedCellAdmission(anchor, proof, releaseDigest, sequence, raw, binding, now)
	if err != nil {
		return nil, err
	}
	var envelope CellAdmissionEnvelope
	if strictDecode(raw, &envelope) != nil {
		return nil, errors.New("invalid admission encoding")
	}
	if err := insertVerifiedAdmission(ctx, db, envelope.Admission, verified.AdmissionDigest); err != nil {
		return nil, err
	}
	verified.NonceConsumed = true
	// Consumption is not runtime qualification or deploy authorization.
	return verified, nil
}

const registryInsertSQL = `INSERT INTO cell_admission.consumed
        (network_id,epoch,nonce,cell_id,cluster_uid,operator_id,change_id,approval_digest,admission_digest,issued_at_unix_seconds,expires_at_unix_seconds)
        SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::bigint,$11::bigint
        WHERE extract(epoch FROM clock_timestamp()) >= $10::bigint
          AND extract(epoch FROM clock_timestamp()) < $11::bigint
        ON CONFLICT DO NOTHING RETURNING admission_digest`

func insertVerifiedAdmission(ctx context.Context, db *sql.DB, a CellAdmission, digest string) error {
	if db == nil {
		return errors.New("authenticated admission registry unavailable")
	}
	message, err := json.Marshal(a)
	if err != nil || hashBytes(message) != digest {
		return errors.New("admission consumption digest mismatch")
	}
	// One committed statement; uniqueness serializes competing service instances.
	// Database time rechecks validity after transport/lock delays. No updates or
	// deletes and no client-controlled consumption timestamp are permitted.
	var recorded string
	err = db.QueryRowContext(ctx, registryInsertSQL, a.NetworkID, a.Epoch, a.Nonce, a.CellID, a.ClusterUID,
		a.OperatorID, a.ChangeID, a.ApprovalDigest, digest, a.IssuedAt, a.ExpiresAt).Scan(&recorded)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("admission expired or already consumed; explicit recovery required")
	}
	if err != nil || recorded != digest {
		return errors.New("admission registry outcome uncertain; explicit recovery required")
	}
	return nil
}

// Read-only recovery inspection, not permission to resume runtime effects.
// Reverify current consensus trust and signatures at the authoritative original
// consumption time. Expiry governs initial consumption, not retained history.
func inspectConsumedCellAdmission(ctx context.Context, db *sql.DB, anchor TrustAnchor, proof ConsensusProof,
	releaseDigest string, sequence uint64, raw []byte, binding AdmissionBinding, now time.Time) (*AdmissionVerification, error) {
	consensus, err := verifyConsensus(anchor, proof, releaseDigest, sequence, now)
	if err != nil {
		return nil, err
	}
	var envelope CellAdmissionEnvelope
	if len(raw) > 65536 || strictDecode(raw, &envelope) != nil {
		return nil, errors.New("invalid recovery admission encoding")
	}
	if db == nil {
		return nil, errors.New("authenticated admission registry unavailable")
	}
	a := envelope.Admission
	message, err := json.Marshal(a)
	if err != nil {
		return nil, errors.New("invalid recovery admission payload")
	}
	digest := hashBytes(message)
	var recorded string
	var consumedAt int64
	err = db.QueryRowContext(ctx, `SELECT admission_digest,floor(extract(epoch FROM consumed_at))::bigint
		FROM cell_admission.consumed WHERE network_id=$1 AND epoch=$2 AND nonce=$3
		AND cell_id=$4 AND cluster_uid=$5 AND operator_id=$6 AND change_id=$7 AND approval_digest=$8
		AND issued_at_unix_seconds=$9 AND expires_at_unix_seconds=$10`, a.NetworkID, a.Epoch, a.Nonce, a.CellID, a.ClusterUID,
		a.OperatorID, a.ChangeID, a.ApprovalDigest, a.IssuedAt, a.ExpiresAt).Scan(&recorded, &consumedAt)
	if err != nil || recorded != digest || consumedAt < 0 {
		return nil, errors.New("exact consumed admission unavailable; recovery refused")
	}
	binding.ApprovalDigest = consensus.ApprovalDigest
	verifiedDigest, err := verifyCellAdmission(anchor.Policy, raw, binding, uint64(consumedAt))
	if err != nil || verifiedDigest != digest {
		return nil, errors.New("consumed admission verification failed")
	}
	return &AdmissionVerification{Schema: "kerosene.cell-admission-verification/v1", AdmissionDigest: digest, Consensus: consensus, NonceConsumed: true}, nil
}
