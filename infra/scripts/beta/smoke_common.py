#!/usr/bin/env python3
"""Shared helpers for Kerosene beta smoke scripts."""
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
PASSWORD = os.environ.get("SMOKE_PASSWORD", "BetaSmoke#2026ab")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}")


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
        with urllib.request.urlopen(req, timeout=45) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
    if isinstance(expected, int):
        expected = (expected,)
    if status not in expected:
        fail(f"{method} {url} -> HTTP {status}: {raw[:900]}")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fail(f"{method} {url} non-JSON: {raw[:400]}")
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


def stop_port_forwards(*procs: subprocess.Popen[Any] | None) -> None:
    for proc in procs:
        if proc is None:
            continue
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def signup_and_finalize(label: str = "beta") -> dict[str, Any]:
    """Returns dict with username, password, user_id, jwt, device key material."""
    pow_resp = http_json("GET", f"{SERVER}/auth/pow/challenge")
    challenge = pow_resp["data"]["challenge"]
    nonce = solve_pow(challenge)
    username = f"{label}{uuid.uuid4().hex[:10]}"
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
    session_id = signup["data"]["sessionId"]

    start = http_json(
        "POST",
        f"{SERVER}/auth/device-key/onboarding/start?sessionId={session_id}&username={username}",
    )
    ch = start["data"]
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes_raw()
    public_b64 = b64url(public_raw)
    public_sha = b64url(hashlib.sha256(public_raw).digest())
    credential_id = b64url(uuid.uuid4().bytes)
    device_install_id = str(uuid.uuid4())
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
    finish = http_json(
        "POST",
        f"{SERVER}/auth/device-key/onboarding/finish?sessionId={session_id}",
        {
            "publicKey": public_b64,
            "publicKeySha256": public_sha,
            "credentialId": credential_id,
            "userHandle": credential_id,
            "deviceName": f"{label}-device",
            "deviceInstallId": device_install_id,
            "keyStorage": "SECURE_STORAGE",
            "platform": "linux",
            "browser": "smoke",
            "brand": "kerosene",
            "model": "e2e",
            "serialNumber": f"{label}-1",
            "signedPayload": signed_payload,
            "signature": signature,
        },
    )
    token_blob = finish["data"]
    user_id, jwt = token_blob.split(" ", 1)
    return {
        "username": username,
        "password": PASSWORD,
        "user_id": user_id,
        "jwt": jwt,
        "device_install_id": device_install_id,
        "device_hash": device_install_id,
        "credential_id": credential_id,
        "device_counter": 1,
        "private_key": private_key,
        "onion_service_id": ch["onionServiceId"],
    }


def build_device_key_auth_assertion(user: dict[str, Any]) -> str:
    """Build DEVICE_KEY assertion JSON for KFE custodial transfer authorization."""
    # Issue auth challenge (public endpoint)
    ch_resp = http_json(
        "GET",
        f"{SERVER}/auth/device-key/challenge?username={user['username']}",
    )
    ch = ch_resp["data"]
    counter = int(user.get("device_counter", 1)) + 1
    user["device_counter"] = counter
    issued_at = int(time.time())
    payload = {
        "challenge": ch["challenge"],
        "challengeId": ch["challengeId"],
        "counter": counter,
        "credentialId": user["credential_id"],
        "deviceInstallId": user["device_install_id"],
        "issuedAtEpochSeconds": issued_at,
        "onionServiceId": ch.get("onionServiceId") or user.get("onion_service_id"),
        "type": "AUTH_DEVICE_KEY",
        "username": user["username"].lower(),
        "version": 1,
    }
    signed_payload = canonicalize(payload)
    private_key: Ed25519PrivateKey = user["private_key"]
    signature = b64url(private_key.sign(signed_payload.encode("utf-8")))
    assertion = {
        "type": "DEVICE_KEY",
        "credentialId": user["credential_id"],
        "deviceInstallId": user["device_install_id"],
        "signedPayload": signed_payload,
        "signature": signature,
    }
    return json.dumps(assertion, separators=(",", ":"))


def auth_headers(user: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user['jwt']}",
        "X-Device-Hash": user.get("device_hash", user["device_install_id"]),
    }


def bitcoin_cli(*args: str) -> str:
    cmd = [
        KUBECTL,
        "-n",
        NS,
        "exec",
        "deploy/local-bitcoin",
        "--",
        "sh",
        "-c",
        "/opt/bitcoin-28.0/bin/bitcoin-cli -datadir=/home/bitcoin/.bitcoin "
        "-rpcuser=kerosene -rpcpassword=kerosene-local-rpc "
        + " ".join(args),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"bitcoin-cli {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result.stdout.strip()
