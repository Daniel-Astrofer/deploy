#!/usr/bin/env python3
"""Export UNTRUSTED proofs from a disposable four-validator loopback RPC lab.

Transport is not trust. The installed Go verifier must verify every proof
against an independently selected anchor. Never provisions production anchors.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CHAIN = "bank-release-governance"
MAX_BYTES = 8 * 1024 * 1024
MAX_HEIGHT = 4096


class ProofError(ValueError):
    pass


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProofError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ProofError("unsupported JSON number")

    if len(raw) > MAX_BYTES:
        raise ProofError("JSON byte limit exceeded")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=invalid, parse_constant=invalid)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProofError("invalid bounded JSON") from exc


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def read_document(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProofError("input must be a regular file")
        return decode(stream.read(MAX_BYTES + 1))


def write_document(path, value):
    """Exclusive, fsynced publication in a private lab directory."""
    path = Path(path).absolute()
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProofError("output parent must be an operator-owned private real directory")
    raw = encoded(value) + b"\n"
    if len(raw) > MAX_BYTES:
        raise ProofError("proof byte limit exceeded")
    fd, temporary = tempfile.mkstemp(prefix=".proof-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProofError("loopback RPC redirects are forbidden")


class RPC:
    def __init__(self, url, *, timeout=3, total_seconds=90):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1") or
                parsed.port is None or parsed.username or parsed.password or parsed.query or
                parsed.fragment or parsed.path not in ("", "/")):
            raise ProofError("RPC must be an explicit HTTP loopback lab host and port")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.deadline = time.monotonic() + total_seconds
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method, **params):
        if time.monotonic() >= self.deadline:
            raise ProofError("RPC export deadline exceeded")
        body = encoded({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=min(self.timeout, self.deadline - time.monotonic())) as stream:
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                if time.monotonic() >= self.deadline:
                    raise ProofError("RPC export deadline exceeded")
                chunk = stream.read1(min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
        document = decode(raw)
        if document.get("error") or not isinstance(document.get("result"), dict):
            raise ProofError("RPC method failed: " + method)
        return document["result"]

    def state(self):
        result = self.call("abci_query", path="/release/state", prove=False)
        response = result["response"]
        if int(response.get("code", 0)) != 0:
            raise ProofError("ABCI state query failed")
        state = decode(base64.b64decode(response["value"], validate=True))
        if int(response["height"]) != state["height"]:
            raise ProofError("ABCI response/state height mismatch")
        return state

    def light(self, height):
        commit = self.call("commit", height=str(height))
        signed = commit["signed_header"]
        header = signed["header"]
        if int(header["height"]) != height or header["chain_id"] != CHAIN:
            raise ProofError("wrong RPC commit height/chain")
        response = self.call("validators", height=str(height), page="1", per_page="100")
        validators = response["validators"]
        if (int(response["block_height"]) != height or int(response["total"]) != 4 or
                len(validators) != 4 or any(int(v["voting_power"]) != 1 for v in validators)):
            raise ProofError("lab requires exactly four equal-power consensus validators")
        proposer = next((v for v in validators if v["address"] == header["proposer_address"]), None)
        if proposer is None:
            raise ProofError("header proposer is missing from validator set")
        return {"signed_header": signed, "validator_set": {
            "validators": validators, "proposer": proposer, "total_voting_power": "4"}}


def ordered_approval(approval):
    fields = ("epoch", "networkId", "previousApprovalDigest", "releaseLockCanonicalDigest", "schema", "sequence")
    if set(approval) != set(fields):
        raise ProofError("unknown approval fields")
    return {key: approval[key] for key in fields}


def ordered_state(height, policy, approvals):
    """Go State/Policy field order, not alphabetically sorted outer objects."""
    return {"height": height, "policy": {
        "networkId": policy["networkId"], "epoch": policy["epoch"], "threshold": policy["threshold"],
        "members": {key: policy["members"][key] for key in sorted(policy["members"])}},
        "approvals": [ordered_approval(a) for a in approvals]}


def policy_check(policy):
    if (set(policy) != {"networkId", "epoch", "threshold", "members"} or
            policy["networkId"] != CHAIN or policy["epoch"] != 1 or policy["threshold"] != 3 or
            not isinstance(policy["members"], dict) or len(policy["members"]) != 4):
        raise ProofError("lab requires the explicit static four-authorizer 3/4 policy")


def export_anchor(rpc, height, policy, trusting_period=600):
    if not 1 <= height <= MAX_HEIGHT or not 1 <= trusting_period <= 3600:
        raise ProofError("anchor height/trusting-period outside lab limits")
    policy_check(policy)
    return {"lightBlock": rpc.light(height), "policy": policy,
            "trustingPeriodSeconds": trusting_period}


def export_proof(rpc, anchor, target_height):
    policy = anchor["policy"]
    policy_check(policy)
    anchor_height = int(anchor["lightBlock"]["signed_header"]["header"]["height"])
    if not 1 <= anchor_height < target_height < MAX_HEIGHT:
        raise ProofError("approval height must follow anchor within the bounded lab history")
    latest = rpc.state()
    if latest["policy"] != policy or latest["height"] < target_height:
        raise ProofError("live app policy/height differs from anchor")
    # The app has no historical query store. Reconstruct target state using
    # actual executed transactions, not the later latest-height snapshot.
    approvals = []
    target_transactions = None
    for height in range(1, target_height + 1):
        block = rpc.call("block", height=str(height))["block"]
        if int(block["header"]["height"]) != height or block["header"]["chain_id"] != CHAIN:
            raise ProofError("wrong block history height/chain")
        transactions = block["data"].get("txs") or []
        results = rpc.call("block_results", height=str(height)).get("txs_results") or []
        if len(transactions) != len(results):
            raise ProofError("transaction/execution-result count mismatch")
        for transaction, result in zip(transactions, results):
            raw = base64.b64decode(transaction, validate=True)
            if len(raw) > 65536:
                raise ProofError("transaction byte limit exceeded")
            if int(result.get("code", 0)) == 0:
                proposal = decode(raw)
                approval = ordered_approval(proposal["approval"])
                if approval["sequence"] != len(approvals) + 1:
                    raise ProofError("nonsequential successful application transaction")
                approvals.append(approval)
        if height == target_height:
            target_transactions = transactions
    if approvals != latest["approvals"][:len(approvals)]:
        raise ProofError("reconstructed approvals differ from current committed history")
    state = ordered_state(target_height, policy, approvals)
    blocks = [rpc.light(height) for height in range(anchor_height + 1, target_height + 2)]
    committed_hash = blocks[-1]["signed_header"]["header"]["app_hash"]
    if hashlib.sha256(encoded(state)).hexdigest().upper() != committed_hash.upper():
        raise ProofError("reconstructed target state does not match following header AppHash")
    proof = {"schema": "kerosene.release-consensus-proof/v1", "blocks": blocks,
             "approvalBlockHeight": target_height, "transactions": target_transactions, "state": state}
    if len(encoded(proof)) > MAX_BYTES:
        raise ProofError("proof byte limit exceeded")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("anchor", "proof"):
        sub = commands.add_parser(command)
        sub.add_argument("--rpc", required=True)
        sub.add_argument("--output", required=True)
        if command == "anchor":
            sub.add_argument("--height", type=int, required=True)
            sub.add_argument("--policy", required=True)
            sub.add_argument("--trusting-period", type=int, default=600)
        else:
            sub.add_argument("--trusted-anchor", required=True)
            sub.add_argument("--approval-height", type=int, required=True)
    args = parser.parse_args()
    try:
        rpc = RPC(args.rpc)
        if args.command == "anchor":
            result = export_anchor(rpc, args.height, read_document(args.policy), args.trusting_period)
        else:
            result = export_proof(rpc, read_document(args.trusted_anchor), args.approval_height)
        write_document(args.output, result)
        print(json.dumps({"exported": str(Path(args.output).absolute()), "cryptographicallyVerified": False}))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, "untrusted lab proof export failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
