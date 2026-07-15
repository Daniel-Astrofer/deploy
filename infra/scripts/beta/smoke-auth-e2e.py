#!/usr/bin/env python3
"""
Beta auth E2E smoke:
  PoW → signup → device-key onboarding → JWT → /auth/me → /kfe/wallets → password login

Usage:
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  # optional: SERVER_URL=http://127.0.0.1:18080 KFE_URL=http://127.0.0.1:18081
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-auth-e2e.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SERVER = os.environ.get("SERVER_URL", "http://127.0.0.1:18080").rstrip("/")
KFE = os.environ.get("KFE_URL", "http://127.0.0.1:18081").rstrip("/")
NS = os.environ.get("KEROSENE_NAMESPACE", "kerosene-local")
KUBECTL = os.environ.get("KUBECTL", "kubectl")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected: int | tuple[int, ...] = 200,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req_headers = {"Accept": "application/json"}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
    if isinstance(expected, int):
        expected = (expected,)
    if status not in expected:
        fail(f"{method} {url} -> HTTP {status}: {raw[:800]}")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fail(f"{method} {url} non-JSON response: {raw[:400]}")
        return {}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def solve_pow(challenge: str, max_iters: int = 5_000_000) -> str:
    for nonce in range(max_iters):
        nonce_s = str(nonce)
        digest = hashlib.sha256((challenge + nonce_s).encode("utf-8")).hexdigest()
        if digest.startswith("0000"):
            return nonce_s
    fail("PoW not solved within iteration budget")
    return ""


def canonicalize(values: dict[str, Any]) -> str:
    """Match DeviceKeyCanonicalJson: TreeMap order, no spaces."""
    parts: list[str] = []
    for key in sorted(values.keys()):
        value = values[key]
        if isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        else:
            fail(f"unsupported canonical value type: {type(value)}")
            rendered = ""
        parts.append(f"{json.dumps(key, ensure_ascii=False)}:{rendered}")
    return "{" + ",".join(parts) + "}"


def start_port_forwards() -> tuple[subprocess.Popen[Any], subprocess.Popen[Any]]:
    pf1 = subprocess.Popen(
        [KUBECTL, "-n", NS, "port-forward", "svc/server", "18080:8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pf2 = subprocess.Popen(
        [KUBECTL, "-n", NS, "port-forward", "svc/kfe-service", "18081:8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    return pf1, pf2


def main() -> None:
    managed_pf = False
    pf1 = pf2 = None
    if "SERVER_URL" not in os.environ:
        managed_pf = True
        pf1, pf2 = start_port_forwards()

    try:
        # 1) PoW
        pow_resp = http_json("GET", f"{SERVER}/auth/pow/challenge")
        if not pow_resp.get("success"):
            fail(f"pow challenge failed: {pow_resp}")
        challenge = pow_resp["data"]["challenge"]
        nonce = solve_pow(challenge)
        ok(f"PoW solved (nonce={nonce})")

        # 2) Signup
        username = f"beta{uuid.uuid4().hex[:10]}"
        password = "BetaSmoke#2026ab"
        signup = http_json(
            "POST",
            f"{SERVER}/auth/signup",
            {
                "username": username,
                "password": password,
                "challenge": challenge,
                "nonce": nonce,
                "accountSecurity": "STANDARD",
            },
        )
        if not signup.get("success"):
            fail(f"signup failed: {signup}")
        session_id = signup["data"]["sessionId"]
        ok(f"signup session created for {username}")

        # 3) Device-key onboarding start
        start = http_json(
            "POST",
            f"{SERVER}/auth/device-key/onboarding/start?sessionId={session_id}&username={username}",
        )
        if not start.get("success"):
            fail(f"device-key start failed: {start}")
        ch = start["data"]
        challenge_id = ch["challengeId"]
        challenge_value = ch["challenge"]
        onion_service_id = ch["onionServiceId"]
        ok(f"device-key challenge onionServiceId={onion_service_id}")

        # 4) Ed25519 register + finish
        private_key = Ed25519PrivateKey.generate()
        public_raw = private_key.public_key().public_bytes_raw()
        public_b64 = b64url(public_raw)
        public_sha = b64url(hashlib.sha256(public_raw).digest())
        credential_id = b64url(uuid.uuid4().bytes)
        device_install_id = str(uuid.uuid4())
        issued_at = int(time.time())
        counter = 1
        payload = {
            "algorithm": "Ed25519",
            "challenge": challenge_value,
            "challengeId": challenge_id,
            "counter": counter,
            "credentialId": credential_id,
            "deviceInstallId": device_install_id,
            "issuedAtEpochSeconds": issued_at,
            "onionServiceId": onion_service_id,
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
            "deviceName": "beta-smoke-device",
            "deviceInstallId": device_install_id,
            "keyStorage": "SECURE_STORAGE",
            "platform": "linux",
            "browser": "smoke",
            "brand": "kerosene",
            "model": "e2e",
            "serialNumber": "smoke-1",
            "signedPayload": signed_payload,
            "signature": signature,
        }
        finish = http_json(
            "POST",
            f"{SERVER}/auth/device-key/onboarding/finish?sessionId={session_id}",
            finish_body,
        )
        if not finish.get("success"):
            fail(f"device-key finish failed: {finish}")
        token_blob = finish["data"]
        if " " not in token_blob:
            fail(f"unexpected token format: {token_blob[:120]}")
        user_id, jwt = token_blob.split(" ", 1)
        ok(f"account finalized userId={user_id}")

        # 5) /auth/me
        me = http_json(
            "GET",
            f"{SERVER}/auth/me",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        if not me.get("success"):
            fail(f"/auth/me failed: {me}")
        ok(f"/auth/me ok keys={list((me.get('data') or {}).keys())[:8]}")

        # 6) KFE wallets
        wallets = http_json(
            "GET",
            f"{KFE}/kfe/wallets",
            headers={"Authorization": f"Bearer {jwt}"},
            expected=(200, 202),
        )
        if not wallets.get("success", True) and "data" not in wallets:
            # some endpoints return list directly or envelope
            fail(f"/kfe/wallets unexpected: {wallets}")
        data = wallets.get("data", wallets)
        count = len(data) if isinstance(data, list) else (
            len(data.get("wallets", [])) if isinstance(data, dict) else 0
        )
        ok(f"/kfe/wallets reachable (items~{count})")

        # 7) Password login (no TOTP yet)
        login = http_json(
            "POST",
            f"{SERVER}/auth/login",
            {"username": username, "password": password},
            expected=(200, 202),
        )
        if not login.get("success"):
            fail(f"login failed: {login}")
        login_data = login.get("data") or ""
        if " " in str(login_data):
            ok("password login issued JWT session")
        else:
            ok(f"password login accepted (pre-auth/token={str(login_data)[:40]}…)")

        # 8) Logout
        logout = http_json(
            "POST",
            f"{SERVER}/auth/logout",
            headers={"Authorization": f"Bearer {jwt}"},
            expected=(200, 401),
        )
        ok(f"logout status success={logout.get('success')}")

        print("[+] smoke-auth-e2e passed")
        print(f"[+] username={username} userId={user_id}")
    finally:
        if managed_pf:
            for proc in (pf1, pf2):
                if proc is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()


if __name__ == "__main__":
    main()
