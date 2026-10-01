#!/usr/bin/env bash
# Real disposable consensus integration. Never uses existing Cell directories.
set -euo pipefail
umask 077
governance_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test_root=$(mktemp -d /tmp/kerosene-governance-testnet.XXXXXX)
cleanup() {
  # This exact directory was allocated by this invocation; never accept a
  # user-supplied recursive-delete target or use pkill/killall.
  case "$test_root" in /tmp/kerosene-governance-testnet.??????) ;; *) return 1 ;; esac
  if [[ ${KEEP_EVIDENCE:-0} == 1 && -d "$test_root/evidence" ]]; then
    evidence_root=$(mktemp -d /tmp/kerosene-governance-evidence.XXXXXX)
    cp -R -- "$test_root/evidence/." "$evidence_root/"
    printf 'Public synthetic evidence: %s\n' "$evidence_root"
  fi
  rm -rf -- "$test_root"
}
trap cleanup EXIT
mkdir -m 700 -- "$test_root/bin"
consensus_binary=${GOVERNANCE_BINARY:-$test_root/bin/kerosene-release-consensus}
comet_binary=${COMETBFT_BINARY:-$test_root/bin/cometbft}
if [[ -z ${GOVERNANCE_BINARY:-} ]]; then
  (cd -- "$governance_dir" && timeout 300s go build -mod=readonly -o "$consensus_binary" .)
fi
if [[ -z ${COMETBFT_BINARY:-} ]]; then
  GOBIN="$test_root/bin" timeout 300s go install github.com/cometbft/cometbft/cmd/cometbft@v0.38.17
fi
[[ -x "$consensus_binary" && -x "$comet_binary" ]]
[[ $("$comet_binary" version) == '0.38.17' ]]
export PYTHONDONTWRITEBYTECODE=1
python3 - "$governance_dir" "$test_root" "$consensus_binary" "$comet_binary" <<'PY' &
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time

directory, root, app, comet = map(Path, sys.argv[1:])
app, comet = app.absolute(), comet.absolute()
spec = importlib.util.spec_from_file_location('lab_export', directory / 'export-proof.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)
evidence = root / 'evidence'
evidence.mkdir(mode=0o700)
logs = root / 'logs'
logs.mkdir(mode=0o700)
nodes = {}
opened_logs = []
ports = []
reservation = []
started = time.monotonic()
deadline = started + 240

def interrupt(signum, frame):
    raise KeyboardInterrupt('test interrupted')

signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)

def command(args, **options):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True,
                          timeout=30, **options)

def wait_for(description, predicate, seconds=45):
    until = min(time.monotonic() + seconds, deadline)
    last_error = None
    while time.monotonic() < until:
        for processes in nodes.values():
            if any(process.poll() is not None for process in processes):
                raise RuntimeError('owned node/app process exited during ' + description)
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError, KeyError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError('deadline waiting for ' + description + ': ' + str(last_error))

def rpc(index=0):
    return export.RPC('http://127.0.0.1:' + str(ports[index * 2]), total_seconds=90)

def state(index=0):
    return rpc(index).state()

def stop_node(index):
    processes = nodes.pop(index, [])
    # Stop Comet before its ABCI app; only exact Popen children are signalled.
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

def start_node(index):
    home = root / 'network' / ('node' + str(index))
    application = home / 'application'
    abci = home / 'abci'
    application.mkdir(mode=0o700, exist_ok=True)
    abci.mkdir(mode=0o700, exist_ok=True)
    socket_path = abci / 'app.sock'
    if socket_path.exists():
        if not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise RuntimeError('unexpected file at owned ABCI socket')
        socket_path.unlink()
    app_log = open(logs / ('app-' + str(index) + '.log'), 'ab')
    node_log = open(logs / ('comet-' + str(index) + '.log'), 'ab')
    opened_logs.extend([app_log, node_log])
    processes = []
    nodes[index] = processes
    processes.append(subprocess.Popen([str(app), 'serve', '--policy', str(root / 'policy.json'),
                                      '--state', str(application / 'state.json'),
                                      '--listen', 'unix://' + str(socket_path)],
                                     stdout=app_log, stderr=app_log))
    wait_for('private ABCI socket ' + str(index), lambda: socket_path.exists(), 10)
    if socket_path.stat().st_mode & 0o077:
        raise RuntimeError('ABCI socket is not private')
    processes.append(subprocess.Popen([str(comet), 'start', '--home', str(home)],
                                     stdout=node_log, stderr=node_log))

def config_value(text, section, key, value):
    expression = r'(?ms)(^\[' + re.escape(section) + r'\]\n)(.*?)(?=^\[|\Z)' if section else r'(?ms)\A(.*?)(?=^\[|\Z)'
    match = re.search(expression, text)
    if not match:
        raise RuntimeError('missing Comet config section ' + section)
    body = match.group(2) if section else match.group(1)
    body, count = re.subn(r'(?m)^' + re.escape(key) + r'\s*=.*$', key + ' = ' + value, body)
    if count != 1:
        raise RuntimeError('missing/duplicate Comet config key ' + key)
    begin, end = match.span(2 if section else 1)
    return text[:begin] + body + text[end:]

def proposal(sequence, previous, release):
    approval = {'epoch': 1, 'networkId': export.CHAIN, 'previousApprovalDigest': previous,
                'releaseLockCanonicalDigest': release, 'schema': 'kerosene.release-approval/v1',
                'sequence': sequence}
    raw = export.encoded(approval)
    message = root / 'approval-to-sign.json'
    message.write_bytes(raw)
    signatures = []
    for index in range(3):
        signature = command(['openssl', 'pkeyutl', '-sign', '-rawin', '-inkey',
                             root / 'authorizers' / (str(index) + '.pem'), '-in', message]).stdout
        if len(signature) != 64:
            raise RuntimeError('invalid Ed25519 signature size')
        signatures.append({'memberId': 'authorizer-' + str(index + 1),
                           'signatureBase64': base64.b64encode(signature).decode()})
    return {'approval': approval, 'signatures': signatures}

def submit(value):
    raw = export.encoded(value)
    result = rpc().call('broadcast_tx_sync', tx=base64.b64encode(raw).decode())
    if int(result.get('code', 0)) != 0:
        raise RuntimeError('signed synthetic proposal was rejected by CheckTx')
    return result['hash']

def verify(anchor_path, proof_path, release, sequence, accepted=True):
    result = subprocess.run([str(app), 'verify', '--trusted-anchor', str(anchor_path),
                             '--proof', str(proof_path), '--release-digest', release,
                             '--sequence', str(sequence)], capture_output=True, timeout=15)
    if accepted:
        if result.returncode:
            raise RuntimeError('offline verifier rejected real RPC proof: ' + result.stderr.decode())
        return export.decode(result.stdout)
    if result.returncode == 0:
        raise RuntimeError('offline verifier accepted negative proof')
    return result.stderr.decode().strip()

def approved(sequence, anchor, release, label):
    decision = wait_for('committed approval ' + str(sequence),
                        lambda: (s if len(s['approvals']) >= sequence else None) if (s := state()) else None)
    approval = decision['approvals'][sequence - 1]
    if approval['releaseLockCanonicalDigest'] != release:
        raise RuntimeError('wrong committed release digest')
    # Find the actual transaction's block height, never infer it from Query's
    # changing latest committed height or from authorizer signature count.
    tx_hash = hashlib.sha256(export.encoded(proposals[sequence])).hexdigest().upper()
    # JSON-RPC hash is []byte/base64, unlike the URI endpoint's 0xHEX form.
    tx_result = wait_for('indexed approval transaction',
                         lambda: rpc().call('tx', hash=base64.b64encode(bytes.fromhex(tx_hash)).decode()))
    height = int(tx_result['height'])
    if int(tx_result['tx_result'].get('code', 0)):
        raise RuntimeError('consensus transaction execution failed')
    wait_for('following committed app-hash header',
             lambda: rpc().call('status')['sync_info']['latest_block_height']
             if int(rpc().call('status')['sync_info']['latest_block_height']) >= height + 1 else None)
    proof = export.export_proof(rpc(), anchor, height)
    path = evidence / (label + '-proof.json')
    export.write_document(path, proof)
    result = verify(evidence / 'anchor.json', path, release, sequence)
    export.write_document(evidence / (label + '-verified.json'), result)
    block = next(b for b in proof['blocks'] if int(b['signed_header']['header']['height']) == height)
    votes = sum(int(s['block_id_flag']) == 2 for s in block['signed_header']['commit']['signatures'])
    return result, proof, path, votes

proposals = {}
try:
    # Inspect linked Go module versions, not just executable filenames.
    for binary in (app, comet):
        metadata = command(['go', 'version', '-m', binary]).stdout.decode()
        if not re.search(r'\bgithub\.com/cometbft/cometbft\s+v0\.38\.17\b', metadata):
            raise RuntimeError('binary does not embed pinned CometBFT v0.38.17: ' + str(binary))
    command([comet, 'testnet', '--v', '4', '--o', root / 'network', '--populate-persistent-peers=false'])
    for path in (root / 'network').rglob('*'):
        if path.is_dir():
            path.chmod(0o700)
    (root / 'network').chmod(0o700)
    genesis = export.read_document(root / 'network/node0/config/genesis.json')
    genesis['chain_id'] = export.CHAIN
    genesis['initial_height'] = '1'
    # testnet generated a current UTC genesis time; no historical timestamps,
    # mocked clocks, or detached signatures stand in for live consensus.
    if len(genesis['validators']) != 4 or any(int(v['power']) != 1 for v in genesis['validators']):
        raise RuntimeError('testnet did not generate four equal-power validators')
    authorizers = root / 'authorizers'
    authorizers.mkdir(mode=0o700)
    policy = {'networkId': export.CHAIN, 'epoch': 1, 'threshold': 3, 'members': {}}
    for index in range(4):
        key = authorizers / (str(index) + '.pem')
        command(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', key])
        public = command(['openssl', 'pkey', '-in', key, '-pubout', '-outform', 'DER']).stdout
        if len(public) != 44 or public[:12].hex() != '302a300506032b6570032100':
            raise RuntimeError('unexpected Ed25519 public key encoding')
        policy['members']['authorizer-' + str(index + 1)] = base64.b64encode(public[12:]).decode()
    if set(policy['members'].values()) & {v['pub_key']['value'] for v in genesis['validators']}:
        raise RuntimeError('proposal authorizers and consensus validators are not disjoint')
    (root / 'policy.json').write_bytes(export.encoded(policy))
    export.write_document(evidence / 'policy.json', policy)
    for _ in range(8):
        reserved = socket.socket()
        reserved.bind(('127.0.0.1', 0))
        reservation.append(reserved)
        ports.append(reserved.getsockname()[1])
    node_ids = [command([comet, 'show-node-id', '--home', root / 'network' / ('node' + str(i))]).stdout.decode().strip()
                for i in range(4)]
    for index in range(4):
        home = root / 'network' / ('node' + str(index))
        (home / 'config/genesis.json').write_bytes(export.encoded(genesis))
        text = (home / 'config/config.toml').read_text()
        peers = ','.join(node_ids[j] + '@127.0.0.1:' + str(ports[j * 2 + 1]) for j in range(4) if j != index)
        values = [('', 'proxy_app', json.dumps('unix://' + str(home / 'abci/app.sock'))),
                  ('', 'log_level', '"error"'),
                  ('rpc', 'laddr', json.dumps('tcp://127.0.0.1:' + str(ports[index * 2]))),
                  ('rpc', 'unsafe', 'false'),
                  ('p2p', 'laddr', json.dumps('tcp://127.0.0.1:' + str(ports[index * 2 + 1]))),
                  ('p2p', 'external_address', '""'), ('p2p', 'persistent_peers', json.dumps(peers)),
                  ('p2p', 'seeds', '""'), ('p2p', 'pex', 'false'), ('p2p', 'addr_book_strict', 'false'),
                  ('p2p', 'allow_duplicate_ip', 'true'), ('consensus', 'timeout_propose', '"1s"'),
                  ('consensus', 'timeout_prevote', '"300ms"'), ('consensus', 'timeout_precommit', '"300ms"'),
                  ('consensus', 'timeout_commit', '"500ms"'), ('consensus', 'create_empty_blocks', 'true'),
                  ('consensus', 'create_empty_blocks_interval', '"0s"')]
        for section, key, value in values:
            text = config_value(text, section, key, value)
        (home / 'config/config.toml').write_text(text)
    for reserved in reservation:
        reserved.close()
    reservation.clear()
    for index in range(4):
        start_node(index)
    wait_for('all four validators connected and committing',
             lambda: all(int(rpc(i).call('status')['sync_info']['latest_block_height']) >= 2 and
                         int(rpc(i).call('net_info')['n_peers']) == 3 for i in range(4)))
    anchor_height = int(rpc().call('status')['sync_info']['latest_block_height'])
    anchor = export.export_anchor(rpc(), anchor_height, policy, trusting_period=600)
    export.write_document(evidence / 'anchor.json', anchor)
    print('Four real validators committing; immutable laboratory anchor height=' + str(anchor_height), flush=True)
    releases = ['sha256:' + digit * 64 for digit in ('a', 'b', 'c')]
    proposals[1] = proposal(1, 'sha256:' + '0' * 64, releases[0])
    submit(proposals[1])
    first, first_proof, first_path, first_votes = approved(1, anchor, releases[0], 'four-online')
    print('4/4 online: real committed approval verified at height=' + str(first['height']), flush=True)
    negative = {}
    for label in ('state-tamper', 'transaction-tamper', 'minority-commit'):
        changed = copy.deepcopy(first_proof)
        if label == 'state-tamper':
            changed['state']['approvals'][0]['releaseLockCanonicalDigest'] = releases[1]
        elif label == 'transaction-tamper':
            changed['transactions'][0] = base64.b64encode(b'{}').decode()
        else:
            signatures = changed['blocks'][-1]['signed_header']['commit']['signatures']
            keep = 2
            for signature in signatures:
                if int(signature['block_id_flag']) == 2 and keep:
                    keep -= 1
                else:
                    signature.update(block_id_flag=1, validator_address='',
                                     timestamp='0001-01-01T00:00:00Z', signature=None)
        path = evidence / (label + '.json')
        export.write_document(path, changed)
        negative[label] = verify(evidence / 'anchor.json', path, releases[0], 1, accepted=False)
    detached = evidence / 'detached-proposal.json'
    export.write_document(detached, proposals[1])
    negative['detached-authorizer-signatures'] = verify(evidence / 'anchor.json', detached, releases[0], 1, accepted=False)
    stop_node(3)
    wait_for('three remaining peers see stopped validator', lambda: all(int(rpc(i).call('net_info')['n_peers']) == 2 for i in range(3)))
    previous = hashlib.sha256(export.encoded(proposals[1]['approval'])).hexdigest()
    proposals[2] = proposal(2, 'sha256:' + previous, releases[1])
    submit(proposals[2])
    second, second_proof, second_path, second_votes = approved(2, anchor, releases[1], 'three-online')
    if second_votes != 3:
        raise RuntimeError('one-validator-offline target did not have exactly three commit votes')
    print('One validator offline: 3/4 commit verified at height=' + str(second['height']), flush=True)
    stop_node(2)
    wait_for('two remaining peers see stopped validators', lambda: all(int(rpc(i).call('net_info')['n_peers']) == 1 for i in range(2)))
    # Let any already-signed in-flight block drain, then fix the observed height.
    time.sleep(2)
    baseline = state()['height']
    previous = hashlib.sha256(export.encoded(proposals[2]['approval'])).hexdigest()
    proposals[3] = proposal(3, 'sha256:' + previous, releases[2])
    tx_hash = submit(proposals[3])
    observed = time.monotonic()
    while time.monotonic() - observed < 8:
        for index in (0, 1):
            latest = state(index)
            if latest['height'] != baseline or len(latest['approvals']) != 2:
                raise RuntimeError('two-validator minority advanced committed state')
        time.sleep(0.25)
    negative['unapproved-third-release'] = verify(evidence / 'anchor.json', second_path, releases[2], 3, accepted=False)
    print('Two validators offline: valid 3/4-authorized proposal remains uncommitted for 8 seconds', flush=True)
    # Restore just the third validator: queued valid proposal should commit.
    start_node(2)
    recovered, recovered_proof, recovered_path, recovered_votes = approved(3, anchor, releases[2], 'recovered-three-online')
    report = {'schema': 'kerosene.release-governance-testnet-result/v1',
              'cometbftVersion': '0.38.17', 'networkId': export.CHAIN,
              'realProcesses': True, 'proposalAuthorizersSeparateFromValidators': True,
              'anchorHeight': anchor_height, 'fourOnline': first, 'fourOnlineCommitVotes': first_votes,
              'oneValidatorOffline': second, 'oneOfflineCommitVotes': second_votes,
              'twoValidatorsOffline': {'observedSeconds': 8, 'height': baseline,
                                       'checkTxAccepted': True, 'approvedDecision': False, 'pendingTxHash': tx_hash},
              'recoveredQuorum': recovered, 'recoveredCommitVotes': recovered_votes,
              'negativeVerificationErrors': negative,
              'elapsedSeconds': round(time.monotonic() - started),
              'proofSha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (first_path, second_path, recovered_path)}}
    export.write_document(evidence / 'result.json', report)
    print(json.dumps(report, sort_keys=True), flush=True)
except BaseException as exc:
    print('Real governance testnet FAILED: ' + str(exc), file=sys.stderr, flush=True)
    for path in logs.glob('*.log'):
        # Only logs produced by these synthetic local processes are exposed.
        print(path.name + ':\n' + path.read_text(errors='replace')[-5000:], file=sys.stderr)
    raise
finally:
    for index in list(nodes):
        stop_node(index)
    for reserved in reservation:
        reserved.close()
    for stream in opened_logs:
        stream.close()
PY
driver_pid=$!
interrupt_driver() {
  trap '' INT TERM
  kill -TERM "$driver_pid" 2>/dev/null || true
  wait "$driver_pid" || true
  exit 130
}
trap interrupt_driver INT TERM
wait "$driver_pid"
