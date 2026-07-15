#!/usr/bin/env python3
"""
Device-binding E2E smoke (1 device → 1 account):

  1) Signup account A with deviceInstallId D (device-key onboarding)
  2) Signup account B on same D without confirm → expect AUTH_024
  3) Finish B with confirmUnlinkDevice=true → success
  4) Password login A still works
  5) Device-key login A fails (credential deleted)
  6) Device-key login B works

Usage:
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-device-binding-e2e.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Reuse helpers from sibling smoke_common when available
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke_common import (  # type: ignore
    SERVER,
    b64url,
    canonicalize,
    fail,
    http_json,
    ok,
    solve_pow,
)

NS = os.environ.get("KEROSENE_NAMESPACE", "kerosene-local")
KUBECTL = os.environ.get("KUBECTL", "kubectl")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "BetaSmoke#2026ab")


def start_port_forward() -> subprocess.Popen[Any] | None:
    if "SERVER_URL" in os.environ:
        return None
    pf = subprocess.Popen(
        [KUBECTL, "-n", NS, "port-forward", "svc/server", "18080:8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    return pf


def pow_and_signup(username: str) -> str:
    pow_resp = http_json("GET", f"{SERVER}/auth/pow/challenge")
    if not pow_resp.get("success"):
        fail(f"pow challenge failed: {pow_resp}")
    challenge = pow_resp["data"]["challenge"]
    nonce = solve_pow(challenge)
    signup = http_json(
        "POST",
        f"{SERVER}/auth/signup",
        {
            "username": username,
            "password": PASSWORD,
            "challenge": challenge,
            "nonce": nonce,
            "accountSecurity": "STANDARD",
        },
    )
    if not signup.get("success"):
        fail(f"signup failed for {username}: {signup}")
    return signup["data"]["sessionId"]


def device_key_finish(
    session_id: str,
    username: str,
    device_install_id: str,
    *,
    confirm_unlink: bool = False,
    expected: int | tuple[int, ...] = 200,
) -> tuple[dict[str, Any], Ed25519PrivateKey, str]:
    start = http_json(
        "POST",
        f"{SERVER}/auth/device-key/onboarding/start?sessionId={session_id}&username={username}",
    )
    if not start.get("success"):
        fail(f"device-key start failed: {start}")
    ch = start["data"]
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes_raw()
    public_b64 = b64url(public_raw)
    public_sha = b64url(hashlib.sha256(public_raw).digest())
    credential_id = b64url(uuid.uuid4().bytes)
    issued_at = int(time.time())
    payload = {
        "algorithm": "Ed25519",
        "challenge": ch["challenge"],
        "challengeId": ch["challengeId"],
        "counter": 1,
        "credentialId": credential_id,
        "deviceInstallId": device_install_id,
        "issuedAtEpochSeconds": issued_at,
        "onionServiceId": ch["onionServiceId"],
        "publicKeySha256": public_sha,
        "sessionId": session_id,
        "type": "REGISTER_DEVICE_KEY",
        "username": username.lower(),
        "version": 1,
    }
    signed_payload = canonicalize(payload)
    signature = b64url(private_key.sign(signed_payload.encode("utf-8")))
    finish_body = {
        "publicKey": public_b64,
        "publicKeySha256": public_sha,
        "credentialId": credential_id,
        "userHandle": credential_id,
        "deviceName": "smoke-binding-device",
        "deviceInstallId": device_install_id,
        "keyStorage": "SECURE_STORAGE",
        "platform": "linux",
        "browser": "smoke",
        "brand": "kerosene",
        "model": "binding-e2e",
        "serialNumber": "bind-1",
        "signedPayload": signed_payload,
        "signature": signature,
        "confirmUnlinkDevice": confirm_unlink,
    }
    return http_json(
        "POST",
        f"{SERVER}/auth/device-key/onboarding/finish?sessionId={session_id}",
        finish_body,
        expected=expected,
    ), private_key, credential_id


def device_key_login(
    username: str,
    private_key: Ed25519PrivateKey,
    credential_id: str,
    device_install_id: str,
    *,
    expected: int | tuple[int, ...] = 200,
) -> dict[str, Any]:
    ch_resp = http_json(
        "GET",
        f"{SERVER}/auth/device-key/challenge?username={username}",
    )
    if not ch_resp.get("success"):
        fail(f"auth challenge failed: {ch_resp}")
    ch = ch_resp["data"]
    issued_at = int(time.time())
    # Auth payload shape must match DeviceKeyService.authenticationPayload (no algorithm field).
    payload = {
        "challenge": ch["challenge"],
        "challengeId": ch["challengeId"],
        "counter": 2,
        "credentialId": credential_id,
        "deviceInstallId": device_install_id,
        "issuedAtEpochSeconds": issued_at,
        "onionServiceId": ch["onionServiceId"],
        "type": "AUTH_DEVICE_KEY",
        "username": username.lower(),
        "version": 1,
    }
    signed_payload = canonicalize(payload)
    signature = b64url(private_key.sign(signed_payload.encode("utf-8")))
    return http_json(
        "POST",
        f"{SERVER}/auth/device-key/verify",
        {
            "username": username,
            "credentialId": credential_id,
            "deviceInstallId": device_install_id,
            "signedPayload": signed_payload,
            "signature": signature,
        },
        expected=expected,
    )


def main() -> None:
    pf = start_port_forward()
    try:
        device_install_id = str(uuid.uuid4())
        user_a = f"binda{uuid.uuid4().hex[:8]}"
        user_b = f"bindb{uuid.uuid4().hex[:8]}"

        # Account A
        session_a = pow_and_signup(user_a)
        finish_a, key_a, cred_a = device_key_finish(
            session_a, user_a, device_install_id, confirm_unlink=False
        )
        if not finish_a.get("success"):
            fail(f"account A finish failed: {finish_a}")
        ok(f"account A bound to device {device_install_id[:8]}…")

        # Account B without confirm → AUTH_024
        session_b = pow_and_signup(user_b)
        conflict, _, _ = device_key_finish(
            session_b,
            user_b,
            device_install_id,
            confirm_unlink=False,
            expected=409,
        )
        err = conflict.get("errorCode") or ""
        action = ""
        data = conflict.get("data")
        if isinstance(data, dict):
            action = str(data.get("action") or "")
        if err != "AUTH_024" and action != "CONFIRM_UNLINK_DEVICE":
            fail(f"expected AUTH_024, got: {json.dumps(conflict)[:500]}")
        ok(f"account B blocked with AUTH_024 (action={action or 'n/a'})")

        # Account B with confirm → success
        finish_b, key_b, cred_b = device_key_finish(
            session_b, user_b, device_install_id, confirm_unlink=True, expected=200
        )
        if not finish_b.get("success"):
            fail(f"account B confirm finish failed: {finish_b}")
        ok("account B claimed device after confirmUnlinkDevice")

        # Password login A still works
        login_a = http_json(
            "POST",
            f"{SERVER}/auth/login",
            {"username": user_a, "password": PASSWORD},
            expected=(200, 202),
        )
        if not login_a.get("success"):
            fail(f"password login A failed: {login_a}")
        ok("password login A still works after unlink")

        # Device-key login A must fail (credential deleted)
        device_key_login(
            user_a,
            key_a,
            cred_a,
            device_install_id,
            expected=(400, 401, 404, 409),
        )
        ok("device-key login A rejected after unlink")

        # Device-key login B works
        login_b = device_key_login(
            user_b, key_b, cred_b, device_install_id, expected=(200, 202)
        )
        if not login_b.get("success"):
            fail(f"device-key login B failed: {login_b}")
        ok("device-key login B works on the device")

        print("[+] smoke-device-binding-e2e passed")
        print(f"[+] userA={user_a} userB={user_b} deviceInstallId={device_install_id}")
    finally:
        if pf is not None:
            pf.terminate()
            try:
                pf.wait(timeout=3)
            except Exception:
                pf.kill()


if __name__ == "__main__":
    main()
